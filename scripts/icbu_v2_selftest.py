#!/usr/bin/env python3
"""用虚构消息检查单独启动的阿里 v2 测试接收端，不读取生产配置。"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import getpass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit
import uuid


class CheckFailed(Exception):
    pass


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    display: str


LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


def target_for(value, allow_lan=False):
    """只接受 IP 字面值，避免代理、域名重绑定和隐藏在路径里的目标。"""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or '%' in (parsed.hostname or '')
                or any(c.isspace() for c in value)):
            raise ValueError()
        address = ipaddress.ip_address(parsed.hostname or '')
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535 or port == 8791:
            raise ValueError()
    except (ValueError, TypeError):
        raise CheckFailed('地址无效：请填写测试实例的 IP 和端口，不能使用 8791、域名、凭据或额外路径。') from None
    local = address.is_loopback
    lan = any(address.version == network.version and address in network for network in LAN_NETWORKS)
    if not local and not (allow_lan and lan):
        raise CheckFailed('目标不是回环地址；局域网测试实例需要显式加 --allow-lan，公网地址始终拒绝。')
    host = str(address)
    display = f'{parsed.scheme}://[{host}]:{port}' if address.version == 6 else f'{parsed.scheme}://{host}:{port}'
    return Target(parsed.scheme, host, port, display)


class Client:
    def __init__(self, target, token, timeout=10):
        self.target, self.token, self.timeout = target, token, timeout

    def request(self, method, path, payload=None, headers=None):
        connection_type = http.client.HTTPSConnection if self.target.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(self.target.host, self.target.port, timeout=self.timeout)
        supplied = {'Authorization': 'Bearer ' + self.token, **(headers or {})}
        body = payload
        if isinstance(payload, dict):
            body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            supplied['Content-Type'] = 'application/json; charset=utf-8'
        if body is not None:
            supplied['Content-Length'] = str(len(body))
        try:
            # http.client 不读取代理环境变量，也不自动跟随重定向或重发请求。
            connection.request(method, path, body=body, headers=supplied)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise CheckFailed('接收端返回了重定向，工具已停止；请直接填写测试实例地址。')
            raw = response.read(65537)
            if len(raw) > 65536:
                raise CheckFailed('接收端响应过大，工具已停止。')
            try:
                result = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeError):
                raise CheckFailed('接收端响应不是 JSON，请检查测试实例的地址和日志。') from None
            if not isinstance(result, dict):
                raise CheckFailed('接收端响应格式不正确，请检查测试实例。')
            return response.status, result
        except (OSError, http.client.HTTPException):
            raise CheckFailed('请求结果未知，工具没有自动重试。请核对测试实例中的稳定消息 ID 后再处理。') from None
        finally:
            connection.close()


def run(client, output=print):
    """每次运行使用新命名空间；一次运行内始终复用消息和附件 ID。"""
    sequence = uuid.uuid4().hex
    event_a, event_b = 'selftest-' + sequence + '-a', 'selftest-' + sequence + '-b'
    now = datetime.now(timezone.utc).isoformat()
    body = ('这是一份虚构的阿里 v2 联调资料，没有真实客户或订单。\n自测批次：' + sequence + '\n').encode('utf-8')
    digest = hashlib.sha256(body).hexdigest()
    attachment = dict(attachment_id='spec-1', kind='file', filename='fictional-spec.txt',
                      mime='text/plain', size=len(body), sha256=digest)
    message = dict(account='Amy', conversation_id='tm:selftest-' + sequence, buyer_name='虚构联调客户',
                   country='虚构地区', content='[自测虚构消息] 资料与商品卡片',
                   url='https://message.alibaba.com/', message_id='selftest-' + sequence + '-message-a',
                   event_id=event_a, sent_at=now, observed_at=now, history=True,
                   role='seller', probe_version='2.0.0')

    def check(label, result, allowed, *, duplicate=None):
        status, reply = result
        if status not in allowed or (duplicate is not None and reply.get('duplicate') is not duplicate):
            raise CheckFailed(f'{label}失败：HTTP {status}，响应不符合预期。请检查测试实例，不要改用生产端口。')
        if status < 400 and reply.get('ok') is not True:
            raise CheckFailed(f'{label}失败：接收端没有确认成功。请检查测试实例。')
        output(f'{label}：通过（HTTP {status}' + ('，重复请求已识别' if duplicate else '') + '）')
        return reply

    health = check('检查测试接收端', client.request('GET', '/healthz'), {200})
    if health.get('service') != 'icbu-inbound':
        raise CheckFailed('目标不是阿里接收端，请核对测试实例地址。')
    first = check('先登记旧格式虚构消息', client.request('POST', '/api/inbound/alibaba', message), {202}, duplicate=False)
    enriched = dict(message, message_kind='file', attachments=[attachment], cards=[dict(
        card_type='product', title='虚构演示商品', url='https://www.alibaba.com/product-detail/fictional.html',
        product_id='fictional-product', price_text='待核实', moq_text='待核实', subtitle='只用于协议联调')])
    again = check('补充附件和商品卡片', client.request('POST', '/api/inbound/alibaba', enriched), {200}, duplicate=True)
    if not first.get('inbound_id') or first['inbound_id'] != again.get('inbound_id'):
        raise CheckFailed('补充元数据产生了不同的消息记录，请检查接收端去重逻辑。')
    upload_headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': digest}
    path_a = '/api/inbound/alibaba/attachments/' + event_a + '/spec-1'
    check('消息后上传虚构资料', client.request('PUT', path_a, body, upload_headers), {201}, duplicate=False)
    check('重复上传相同资料', client.request('PUT', path_a, body, upload_headers), {200}, duplicate=True)
    path_b = '/api/inbound/alibaba/attachments/' + event_b + '/spec-1'
    check('消息前上传虚构资料', client.request('PUT', path_b, body, upload_headers), {200}, duplicate=True)
    reverse = dict(enriched, event_id=event_b, message_id='selftest-' + sequence + '-message-b', role='buyer')
    check('登记后到的虚构消息', client.request('POST', '/api/inbound/alibaba', reverse), {202}, duplicate=False)
    check('重复登记后到的消息', client.request('POST', '/api/inbound/alibaba', reverse), {200}, duplicate=True)
    bad_hash = dict(upload_headers, **{'X-Content-SHA256': '0' * 64})
    check('拒绝校验和错误的资料', client.request('PUT', '/api/inbound/alibaba/attachments/' + event_b + '/bad-hash',
          body, bad_hash), {400, 409})
    bad_name = dict(enriched, attachments=[dict(attachment, filename='../fictional.txt')])
    check('拒绝可疑文件名', client.request('POST', '/api/inbound/alibaba', bad_name), {400})
    bad_url = dict(enriched, cards=[dict(card_type='link', title='虚构错误链接', url='http://example.invalid/')])
    check('拒绝非 HTTPS 卡片', client.request('POST', '/api/inbound/alibaba', bad_url), {400})
    check('确认原消息仍可查询', client.request('GET', '/api/inbound/alibaba/' + first['inbound_id']), {200})
    output('自测完成：只发送了虚构数据；实际客户数据、浏览器采集和生产环境仍需单独验收。')


def run_v21(client, output=print):
    """显式扩展检查旧事件编号、别名上传与登记读回，不改变 v2 自测调用。"""
    sequence = uuid.uuid4().hex
    event = 'icbu:v1:' + hashlib.sha256(sequence.encode('ascii')).hexdigest()
    alias = 'e1_' + base64.urlsafe_b64encode(event.encode('utf-8')).decode('ascii').rstrip('=')
    body = ('这是一份虚构的旧编号联调资料。\n自测批次：' + sequence + '\n').encode('utf-8')
    digest = hashlib.sha256(body).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    message = dict(account='Amy', conversation_id='tm:selftest-v21-' + sequence,
                   buyer_name='虚构旧编号客户', country='虚构地区', content='[自测虚构消息] 旧事件编号补资料',
                   url='https://message.alibaba.com/', message_id='selftest-v21-' + sequence,
                   event_id=event, sent_at=now, observed_at=now, history=True,
                   role='seller', probe_version='2.1.0')
    attachment = dict(attachment_id='legacy-spec', kind='file', filename='fictional-legacy-spec.txt',
                      mime='text/plain', size=len(body), sha256=digest)
    path = '/api/inbound/alibaba/attachments/' + alias + '/legacy-spec'

    def check(label, result, status, *, duplicate=None, error=None):
        actual, reply = result
        if (actual != status or (actual < 400 and reply.get('ok') is not True)
                or (duplicate is not None and reply.get('duplicate') is not duplicate)
                or (error is not None and reply.get('error') != error)):
            raise CheckFailed(f'{label}失败：HTTP {actual}，响应不符合 v2.1 契约。请核对测试实例和稳定编号。')
        output(f'{label}：通过（HTTP {actual}）')
        return reply

    health = check('检查 v2.1 测试接收端', client.request('GET', '/healthz'), 200)
    if health.get('service') != 'icbu-inbound':
        raise CheckFailed('目标不是阿里接收端，请核对测试实例地址。')
    first = check('登记带冒号的旧编号虚构消息', client.request(
        'POST', '/api/inbound/alibaba', message, {'X-Idempotency-Key': event}), 202, duplicate=False)
    if not first.get('inbound_id') or first.get('event_id') != event or first.get('persisted') is not True:
        raise CheckFailed('旧编号消息没有持久身份回显，请核对测试实例。')
    enriched = dict(message, message_kind='file', attachments=[attachment], cards=[dict(
        card_type='product', title='虚构旧编号商品', product_id='fictional-legacy-product')])
    again = check('为同一旧编号补充附件和卡片', client.request(
        'POST', '/api/inbound/alibaba', enriched, {'X-Idempotency-Key': event}), 200, duplicate=True)
    if again.get('inbound_id') != first['inbound_id'] or again.get('event_id') != event:
        raise CheckFailed('补充资料改变了原消息身份，请核对接收端去重与别名映射。')

    def readback(label, arrived):
        reply = check(label, client.request('GET', path), 200, duplicate=arrived)
        expected = dict(persisted=True, registered=True, file_received=arrived,
                        inbound_id=first['inbound_id'], event_id=event, event_alias=alias,
                        attachment_id=attachment['attachment_id'], kind=attachment['kind'], size=len(body))
        if any(reply.get(key) != value for key, value in expected.items()):
            raise CheckFailed('附件读回的登记、文件或关联身份不一致，请核对原消息和附件编号。')
        if arrived and reply.get('sha256') != digest:
            raise CheckFailed('附件读回的文件校验值不一致，请保留原编号并检查存储。')

    readback('读回已登记但未上传的附件', False)
    upload_headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': digest}
    check('使用传输别名上传旧编号附件', client.request('PUT', path, body, upload_headers), 201, duplicate=False)
    readback('读回确认文件到达且关联原消息', True)
    check('同别名重复上传保持幂等', client.request('PUT', path, body, upload_headers), 200, duplicate=True)
    readback('重复上传后再次核对原消息关联', True)
    long_event = 'icbu:v1:' + 'a' * 86
    too_long = dict(enriched, event_id=long_event, message_id='selftest-v21-long-' + sequence)
    check('拒绝超过93字节的富消息旧编号', client.request(
        'POST', '/api/inbound/alibaba', too_long, {'X-Idempotency-Key': long_event}), 400,
        error='event_id_alias_too_long')
    # 该别名的原文为72字节，无填充；添加字符会解出非法旧编号或非规范尾位。
    check('拒绝被篡改的传输别名', client.request('GET', path.replace(alias, alias + 'A')), 400,
          error='invalid_event_alias')
    check('拒绝非UTF-8传输别名', client.request('GET', path.replace(alias, 'e1__w')), 400,
          error='invalid_event_alias')
    output('v2.1 自测完成：旧编号、补资料、别名上传和读回仅在虚构数据上验证；真实采集仍需单独验收。')


class StorageFault:
    """只操作调用方明确提供的0700临时目录，不覆盖任何已有故障标记。"""
    def __init__(self):
        self.directory = None
        self.identity = None

    def __enter__(self):
        path = Path(os.environ.get('ICBU_V22_TEST_DIR', ''))
        if not path.is_absolute() or Path('/tmp') not in path.parents or '..' in path.parts:
            raise CheckFailed('请把 ICBU_V22_TEST_DIR 设为 /tmp 下测试实例共用的0700临时目录。')
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CheckFailed('故障演练目录必须属于当前身份且权限为0700，请核对临时实例。')
            self.directory, directory = directory, None
            return self
        except OSError:
            raise CheckFailed('无法安全打开临时故障演练目录，请检查路径、权限和符号链接。') from None
        finally:
            if directory is not None:
                os.close(directory)

    def unavailable(self):
        try:
            fd = os.open('attachments-unavailable', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.directory)
        except OSError:
            raise CheckFailed('无法创建故障标记，请确认临时目录中没有其他演练正在运行。') from None
        try:
            info = os.fstat(fd)
            self.identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)

    def restore(self):
        if self.identity is None:
            return
        info = os.stat('attachments-unavailable', dir_fd=self.directory, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != self.identity:
            raise CheckFailed('故障标记已被替换，请人工核对临时实例；工具没有删除其他文件。')
        os.unlink('attachments-unavailable', dir_fd=self.directory)
        self.identity = None

    def __exit__(self, *args):
        try:
            self.restore()
        except OSError:
            raise CheckFailed('无法移除本次故障标记，请人工恢复临时测试实例。') from None
        finally:
            os.close(self.directory)


def run_v22(client, output=print):
    """同机临时实例通过环境变量共用故障目录；不向接收端增加控制接口。"""
    sequence = uuid.uuid4().hex
    event = 'selftest-v22-' + sequence
    now = datetime.now(timezone.utc).isoformat()
    body = b'fictional v22 attachment\n'
    digest = hashlib.sha256(body).hexdigest()
    attachment = dict(attachment_id='spec', kind='file', filename='fictional-v22.txt',
                      mime='text/plain', size=len(body), sha256=digest)
    plain = dict(account='Amy', conversation_id='tm:' + event, buyer_name='虚构断盘测试客户',
                 country='虚构地区', content='[自测虚构消息] 附件离线时文字仍保存',
                 url='https://message.alibaba.com/', event_id=event + '-text', message_id=event + '-text',
                 sent_at=now, observed_at=now, history=True, role='seller', probe_version='2.2.0')
    rich = dict(plain, event_id=event, message_id=event, attachments=[attachment],
                message_kind='file', cards=[dict(card_type='product', title='虚构断盘商品')])
    path = '/api/inbound/alibaba/attachments/' + event + '/spec'
    headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': digest}

    def check(label, method, path, expected, payload=None, headers=None, **fields):
        code, reply = client.request(method, path, payload, headers)
        if code != expected or any(reply.get(key) != value for key, value in fields.items()):
            raise CheckFailed(f'{label}失败：HTTP {code}，响应不符合 v2.2 契约。请核对临时实例和故障目录。')
        output(f'{label}：通过（HTTP {code}）')
        return reply

    def health(state):
        check('检查附件存储状态', 'GET', '/healthz', 200, ok=True,
              service='icbu-inbound', attachments_storage=state)

    with StorageFault() as fault:
        health('ok')
        fault.unavailable()
        health('unavailable')
        first_text = check('离线时保存文字', 'POST', '/api/inbound/alibaba', 202, plain,
                           ok=True, persisted=True, duplicate=False)
        check('离线时文字重放保持幂等', 'POST', '/api/inbound/alibaba', 200, plain,
              ok=True, persisted=True, duplicate=True, inbound_id=first_text['inbound_id'])
        first = check('离线时保存富消息并标记待补', 'POST', '/api/inbound/alibaba', 202, rich,
                      ok=True, persisted=True, attachments_pending=True, attachments_storage='unavailable')
        check('离线时富消息重放保持幂等', 'POST', '/api/inbound/alibaba', 202, rich,
              ok=True, duplicate=True, persisted=True, attachments_pending=True, inbound_id=first['inbound_id'])
        check('离线时仍可查询消息', 'GET', '/api/inbound/alibaba/' + first['inbound_id'], 200,
              ok=True, persisted=True)
        for method in ('PUT', 'GET'):
            check('离线时附件操作明确失败', method, path, 503,
                  body if method == 'PUT' else None, headers if method == 'PUT' else None,
                  error='attachment_storage_unavailable', persisted=False)
        fault.restore()
        health('ok')
        repaired = check('恢复后同事件补齐关联', 'POST', '/api/inbound/alibaba', 200, rich,
                         ok=True, persisted=True, duplicate=True, inbound_id=first['inbound_id'])
        if repaired.get('attachments_pending'):
            raise CheckFailed('附件关联仍待补，请核对临时实例存储；工具没有自动重试。')
        check('恢复后读回登记', 'GET', path, 200, registered=True, file_received=False,
              inbound_id=first['inbound_id'])
        check('恢复后上传虚构附件', 'PUT', path, 201, body, headers, persisted=True)
        check('确认附件到达并关联原消息', 'GET', path, 200, file_received=True,
              sha256=digest, inbound_id=first['inbound_id'])
        fault.unavailable()
        health('unavailable')
        check('再次失联时附件读回失败', 'GET', path, 503, error='attachment_storage_unavailable')
        fault.restore()
        health('ok')
        check('再次恢复后原附件仍可读回', 'GET', path, 200, file_received=True,
              sha256=digest, inbound_id=first['inbound_id'])
    output('v2.2 自测完成：断盘、文字保存和恢复关联仅在虚构数据上验证；真实NAS仍需单独验收。')


class Parser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:', '选项：').replace('optional arguments:', '选项：')

    def error(self, message):
        self.exit(2, '参数不正确，请运行 --help 查看用法。\n')


def main(argv=None):
    parser = Parser(description=__doc__, add_help=False, usage='%(prog)s --url 测试地址 [--v21] [--v22] [--allow-lan] [--dry-run]')
    parser.add_argument('-h', '--help', action='help', help='显示使用说明并退出')
    parser.add_argument('--url', help='本地测试实例地址，例如 http://127.0.0.1:18792；永远拒绝 8791')
    parser.add_argument('--allow-lan', action='store_true', help='显式允许 RFC1918 或 ULA 局域网 IP，运行前回显目标')
    parser.add_argument('--dry-run', action='store_true', help='仅检查并显示目标，不读取测试令牌，不发送请求')
    parser.add_argument('--v21', action='store_true', help='在原有 v2 自测后追加旧编号、别名上传和附件读回检查；默认只跑原有场景')
    parser.add_argument('--v22', action='store_true', help='追加存储失联与恢复检查；仅限同机回环实例，须共用 ICBU_V22_TEST_DIR 临时目录')
    args = parser.parse_args(argv)
    try:
        if not args.url:
            raise CheckFailed('请用 --url 指定单独启动的测试实例，工具没有默认接收端地址。')
        target = target_for(args.url, args.allow_lan)
        if args.v22 and not ipaddress.ip_address(target.host).is_loopback:
            raise CheckFailed('v2.2 故障演练只能连接同机回环测试实例，请核对地址。')
        print('测试目标：' + target.display)
        if args.dry_run:
            print('地址检查通过；没有读取测试令牌，也没有发送请求。')
            return 0
        print('请从标准输入提供测试实例专用的临时令牌；工具不会显示或记录其值。', flush=True)
        token = (getpass.getpass('测试实例临时令牌：') if sys.stdin.isatty() else sys.stdin.readline(258)).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
            raise CheckFailed('测试令牌格式不正确；请使用隔离测试实例的 32–256 位 URL 安全文本。')
        client = Client(target, token)
        run(client)
        if args.v21:
            run_v21(client)
        if args.v22:
            run_v22(client)
        return 0
    except (CheckFailed, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, CheckFailed) else '自测已停止；请核对测试实例，没有自动重试。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
