"""官方 DeepSeek 单次读者契约；原始 HTTP 收据由物理闸门先保存再验收。

导入不读配置、凭据或余额。真实传输只在隔离子进程复用受审客户端的请求
格式与凭据读取函数；不经过 ai-task 的通用文本包装，不重试、不换渠道。
"""
from __future__ import annotations

import ast
import base64
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from types import ModuleType

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).absolute().parent))
from physical_gate import ProviderRejected

MODELS = ('deepseek-flash', 'deepseek-v4-pro')
MAX_BODY = 4 * 1024 * 1024
RECEIPT_SCHEMA = 'kb-official-deepseek-receipt-v1'
PINS = {
    '/opt/imgw/wechat/domestic_digest/client.py': 'a3171dd827223f14dfbf300b78683ce222bf950a411353cb04bd7ba13b1fb6cc',
    '/opt/imgw/wechat/domestic_digest/worker.py': 'deafa4b403c1e138d8b9e4108e1913a999959b154a82e444e25f000778c70463',
    '/opt/imgw/wechat/domestic_digest/inventory.py': 'ef438a6426f5c1db0df12f0d5a4e48cc8e5771b8d57de193919d7697f39f284b',
}
ReceiptRejected = ProviderRejected


def need(ok):
    if not ok:
        raise ValueError('官方读者契约不完整，请保留收据并核对配置')


def reservation_tokens(prompt, schema, model, max_output_tokens=4096, overhead_tokens=1024):
    """只提取已绑定源码中的纯 payload 函数，不导入模型包或读取凭据。"""
    need(model in MODELS and type(overhead_tokens) is int and overhead_tokens >= 1024)
    path = Path('/opt/imgw/wechat/domestic_digest/client.py')
    need(not any(p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1048576)
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == PINS[str(path)])
    functions = [node for node in ast.parse(raw).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'request_payload']
    need(len(functions) == 1)
    # The registered DeepSeek branch uses only json and MAX_PROMPT_CHARS.
    namespace = {'json': json, 'MAX_PROMPT_CHARS': 120000, 'DigestAPIError': ValueError,
                 'BASE_QWEN_MODELS': (), 'INVENTORY_THINKING': {}}
    tree = ast.Module(body=functions, type_ignores=[])
    exec(compile(tree, '<受审请求格式>', 'exec'), namespace)
    payload = namespace['request_payload'](prompt, schema,
        {'provider': 'deepseek', 'model': model}, max_output_tokens=max_output_tokens)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    return len(encoded) + max_output_tokens + overhead_tokens


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def constant(value):
        raise ValueError
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def _schema(schema, value):
    # 只支持现行微信与阿里 schema 的关键词；后续扩展必须重新核对。
    known = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
             'const', 'maxItems', 'minItems', 'maxLength', 'minLength', 'pattern'}
    need(type(schema) is dict and not set(schema) - known)
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool}
    if 'type' in schema:
        need(schema['type'] in types and type(value) is types[schema['type']])
    if 'const' in schema:
        need(type(value) is type(schema['const']) and value == schema['const'])
    if 'enum' in schema:
        need(any(type(value) is type(v) and value == v for v in schema['enum']))
    if type(value) is dict:
        need(all(key in value for key in schema.get('required', [])))
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            need(not set(value) - set(properties))
        for key in value:
            if key in properties:
                _schema(properties[key], value[key])
    if type(value) in (str, list):
        suffix = 'Length' if type(value) is str else 'Items'
        need(len(value) >= schema.get('min' + suffix, 0))
        need(len(value) <= schema.get('max' + suffix, MAX_BODY))
    if type(value) is str and 'pattern' in schema:
        need(re.search(schema['pattern'], value) is not None)
    if type(value) is list and 'items' in schema:
        for item in value:
            _schema(schema['items'], item)


def _check_schema(schema):
    known = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
             'const', 'maxItems', 'minItems', 'maxLength', 'minLength', 'pattern'}
    need(type(schema) is dict and not set(schema) - known)
    if 'properties' in schema:
        need(type(schema['properties']) is dict)
        for child in schema['properties'].values():
            _check_schema(child)
    if 'items' in schema:
        _check_schema(schema['items'])


def _body(receipt, *, operation, model=None):
    need(type(receipt) is dict and receipt.get('schema') == RECEIPT_SCHEMA
         and receipt.get('provider') == 'deepseek_official'
         and receipt.get('operation') == operation
         and receipt.get('requested_model') == model
         and type(receipt.get('http_status')) is int
         and 100 <= receipt['http_status'] <= 599
         and type(receipt.get('received_at')) in (int, float)
         and math.isfinite(receipt['received_at']) and receipt['received_at'] > 0
         and isinstance(receipt.get('body_base64'), str))
    raw = base64.b64decode(receipt['body_base64'], validate=True)
    need(len(raw) <= MAX_BODY)
    status = receipt['http_status']
    codes = {401: 'authentication_failed', 403: 'authentication_failed',
             402: 'insufficient_balance', 429: 'rate_limited'}
    if status in codes:
        raise ReceiptRejected(codes[status], receipt,
                              retry_after_seconds=receipt.get('retry_after_seconds'))
    if status != 200:
        # A complete HTTP error is a known response, never permission to resend.
        raise ReceiptRejected('invalid_response', receipt)
    return raw


def validate_response(receipt, model, schema, evidence_validator, *, max_output_tokens=4096,
                      reservation_tokens=None):
    """仅在 receipt 已持久化后调用；逐字证据仍用原渠道验证函数。"""
    need(model in MODELS and callable(evidence_validator))
    try:
        raw = _body(receipt, operation='completion', model=model)
        value = _json(raw)
        if value.get('model') != model:
            raise ReceiptRejected('model_drift', receipt)
        choices = value['choices']
        need(type(choices) is list and len(choices) == 1)
        choice = choices[0]
        need(choice.get('finish_reason') == 'stop')
        message = choice['message']
        need(type(message) is dict and not any(message.get(k) for k in
             ('tool_calls', 'function_call', 'refusal', 'reasoning_content')))
        content = message['content']
        need(isinstance(content, str) and 0 < len(content.encode()) <= 262144)
        payload = _json(content)
        need(type(payload) is dict)
        _check_schema(schema)
        _schema(schema, payload)
        usage = value['usage']
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        need(type(usage) is dict and all(type(usage.get(k)) is int
             and 0 <= usage[k] <= 1000000 for k in fields))
        usage = {key: usage[key] for key in fields}
        need(usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
             and usage['completion_tokens'] <= max_output_tokens)
        if reservation_tokens is not None:
            need(type(reservation_tokens) is int and usage['total_tokens'] <= reservation_tokens)
        need(evidence_validator(content) is not False)
    except ProviderRejected:
        raise
    except Exception:
        raise ReceiptRejected('invalid_response', receipt) from None
    return {'content': content, 'metadata': {
        'provider': 'deepseek_official', 'requested_model': model, 'actual_model': model,
        'route': 'direct_https', 'json_mode': 'json_object', 'usage': usage,
        'requested_output_tokens': max_output_tokens, 'reservation_tokens': reservation_tokens,
        'thinking_disabled_requested': True, 'finish_reason': 'stop', 'attempts': 1,
        'response_body_sha256': hashlib.sha256(raw).hexdigest(),
    }}


def _micros(value):
    need(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value) is not None)
    try:
        number = Decimal(value)
        need(number.is_finite() and 0 <= number <= Decimal('1000000000'))
        return int((number * 1000000).to_integral_value(rounding=ROUND_FLOOR))
    except InvalidOperation:
        raise ValueError('官方余额格式不完整，请核对只读查询回执') from None


class OfficialDeepSeek:
    def __init__(self, runner=None, *, clock=time.time):
        self.runner = runner or isolated_runner
        self.clock = clock

    def request(self, prompt, schema, model, *, timeout_seconds=45, max_output_tokens=4096):
        need(model in MODELS and isinstance(prompt, str) and 0 < len(prompt) <= 120000
             and type(schema) is dict and type(max_output_tokens) is int
             and 1 <= max_output_tokens <= 8192)
        return self._call({'operation': 'completion', 'model': model, 'prompt': prompt,
                           'schema': deepcopy(schema), 'max_output_tokens': max_output_tokens,
                           'timeout_seconds': timeout_seconds})

    def _call(self, packet):
        timeout = packet['timeout_seconds']
        need(type(timeout) in (int, float) and math.isfinite(timeout) and 1 <= timeout <= 180)
        result = self.runner(deepcopy(packet))
        need(type(result) is dict and set(result) == {'http_status', 'body_base64', 'retry_after_seconds'})
        need(type(result['http_status']) is int and 100 <= result['http_status'] <= 599
             and isinstance(result['body_base64'], str)
             and (result['retry_after_seconds'] is None or type(result['retry_after_seconds']) is int
                  and 0 <= result['retry_after_seconds'] <= 86400))
        need(len(base64.b64decode(result['body_base64'], validate=True)) <= MAX_BODY)
        return {'schema': RECEIPT_SCHEMA, 'provider': 'deepseek_official',
                'operation': packet['operation'], 'requested_model': packet.get('model'),
                'received_at': self.clock(), **result}

    def read_balance(self, *, currency, persist_receipt, timeout_seconds=15):
        need(currency in ('CNY', 'USD') and callable(persist_receipt))
        receipt = self._call({'operation': 'balance', 'timeout_seconds': timeout_seconds})
        persist_receipt(deepcopy(receipt))
        try:
            value = _json(_body(receipt, operation='balance'))
            need(type(value.get('is_available')) is bool and type(value.get('balance_infos')) is list)
            rows = [row for row in value['balance_infos'] if type(row) is dict
                    and row.get('currency') == currency]
            need(len(rows) == 1)
            row = rows[0]
            total, granted, topped = (_micros(row[key]) for key in
                                      ('total_balance', 'granted_balance', 'topped_up_balance'))
            # Rounding down can differ by one micro; never count that difference as credit.
            need(abs(total - granted - topped) <= 1)
        except ProviderRejected:
            raise
        except Exception:
            raise ReceiptRejected('invalid_response', receipt) from None
        return {'provider': 'deepseek_official', 'currency': currency,
                'available': value['is_available'], 'total_micros': min(total, granted + topped),
                'granted_micros': granted, 'topped_up_micros': topped,
                'observed_at': receipt['received_at'], 'receipt': receipt}


def isolated_runner(packet):
    """调用者持久化 provider_started 后才可执行；超时不转换为额度拒绝。"""
    result = subprocess.run([sys.executable, '-I', str(Path(__file__).absolute()), '--worker'],
                            input=json.dumps(packet, ensure_ascii=False), text=True, capture_output=True,
                            timeout=packet['timeout_seconds'], check=False)
    if result.returncode != 0 or len(result.stdout.encode()) > MAX_BODY * 2:
        raise RuntimeError('官方请求结果未知，保留预留并停止全队，禁止重发')
    try:
        value = _json(result.stdout)
        need(type(value) is dict and 'error' not in value)
        return value
    except Exception:
        raise RuntimeError('官方请求结果未知，保留预留并停止全队，禁止重发') from None


def checked_runtime():
    """只读核对源码；不加载包、不访问凭据。"""
    for filename, expected in PINS.items():
        path = Path(filename)
        for component in (path, *path.parents):
            info = component.lstat()
            need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        info = path.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and hashlib.sha256(path.read_bytes()).hexdigest() == expected)


def _worker(packet):
    # This function is never exercised with real transport during development.
    import http.client
    import ssl
    checked_runtime()
    # Avoid package __init__ and unrelated executors: only the three pinned files load.
    package = '_kb_official_digest'
    module = ModuleType(package)
    module.__path__ = ['/opt/imgw/wechat/domestic_digest']
    sys.modules[package] = module
    loaded = {}
    for name in ('inventory', 'client', 'worker'):
        spec = importlib.util.spec_from_file_location(package + '.' + name,
            '/opt/imgw/wechat/domestic_digest/' + name + '.py')
        child = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = child
        spec.loader.exec_module(child)
        loaded[name] = child
    client, worker = loaded['client'], loaded['worker']
    settings = client.registered_settings('deepseek', packet.get('model') or MODELS[0])
    need(client.direct_host_registered(settings['host'], __import__('os').environ))
    operation = packet['operation']
    need(operation in ('completion', 'balance'))
    if operation == 'completion':
        need(packet['model'] in MODELS)
        payload = client.request_payload(packet['prompt'], packet['schema'], settings,
                                         max_output_tokens=packet['max_output_tokens'])
        method, path, body = 'POST', '/chat/completions', json.dumps(
            payload, ensure_ascii=False, separators=(',', ':')).encode()
    else:
        method, path, body = 'GET', '/user/balance', None
    key = worker.read_credential(settings)
    connection = http.client.HTTPSConnection('api.deepseek.com', port=443,
        timeout=packet['timeout_seconds'], context=ssl.create_default_context())
    try:
        connection.request(method, path, body=body, headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
            'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        need(len(raw) <= MAX_BODY)
        retry = response.getheader('Retry-After')
        retry = int(retry) if isinstance(retry, str) and retry.isdigit() and int(retry) <= 86400 else None
        return {'http_status': response.status, 'body_base64': base64.b64encode(raw).decode('ascii'),
                'retry_after_seconds': retry}
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        need(sys.argv[1:] == ['--worker'])
        raw = sys.stdin.read(1024 * 1024 + 1)
        need(len(raw.encode()) <= 1024 * 1024)
        print(json.dumps(_worker(_json(raw)), ensure_ascii=False))
    except BaseException:
        # No exception text, provider body, credentials or headers on stderr/stdout.
        raise SystemExit(1) from None
