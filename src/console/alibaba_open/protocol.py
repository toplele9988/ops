"""Read-only TOP orders and GOP authorization; no implicit retries or logging."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import OpenError


DEFAULT_TOP_URL = 'https://eco.taobao.com/router/rest'
DEFAULT_GOP_URL = 'https://openapi-api.alibaba.com/rest'
DEFAULT_AUTH_URL = 'https://openapi-auth.alibaba.com/oauth/authorize'
DEFAULT_REDIRECT_URI = 'https://www.alibaba.com'
READ_METHODS = ('alibaba.seller.order.list', 'alibaba.seller.order.get')


def top_sign(params, secret):
    raw = ''.join(k + str(params[k]) for k in sorted(params) if k != 'sign')
    return hmac.new(secret.encode('utf8'), raw.encode('utf8'), hashlib.md5).hexdigest().upper()


def gop_sign(path, params, secret):
    raw = path + ''.join(k + str(params[k]) for k in sorted(params) if k != 'sign')
    return hmac.new(secret.encode('utf8'), raw.encode('utf8'), hashlib.sha256).hexdigest().upper()


def _url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise OpenError('接口地址配置不正确，请管理员检查 HTTPS 地址')
    return value.rstrip('/')


def _text(value, maximum=256):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise OpenError('接口返回内容不完整，请管理员核对字段约定')
    return value


def _integer(value, minimum=0):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) < 18:
        value = int(value)
    if type(value) is not int or not minimum <= value <= 253402300799999:
        raise OpenError('接口返回时间或分页格式不正确，请管理员核对字段约定')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_transport(method, url, params, timeout):
    request = Request(url, data=urlencode(params).encode('utf8'), method=method,
                      headers={'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
                               'Accept': 'application/json'})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        return status, {}
    if len(body) > 2 * 1024 * 1024:
        raise OpenError('接口返回过大，请管理员核对接口设置')
    try:
        return status, json.loads(body)
    except (ValueError, UnicodeError):
        raise OpenError('接口返回格式不正确，请管理员核对接口设置') from None


class Client:
    def __init__(self, credentials, transport=None, clock=None, top_url=None, gop_url=None,
                 auth_url=None, redirect_uri=None, *, enable_network=False,
                 top_timestamp_mode=None, before_request=None):
        self.credentials = credentials
        self.transport = transport
        self.clock = clock or time.time
        self.top_url = _url(top_url or os.environ.get('OPS_ALIBABA_OPEN_TOP_URL', DEFAULT_TOP_URL))
        self.gop_url = _url(gop_url or os.environ.get('OPS_ALIBABA_OPEN_GOP_URL', DEFAULT_GOP_URL))
        self.auth_url = _url(auth_url or os.environ.get('OPS_ALIBABA_OPEN_AUTH_URL', DEFAULT_AUTH_URL))
        self.redirect_uri = _url(redirect_uri or os.environ.get('OPS_ALIBABA_OPEN_REDIRECT_URI', DEFAULT_REDIRECT_URI))
        self.enable_network = enable_network
        self.top_timestamp_mode = top_timestamp_mode or os.environ.get('OPS_ALIBABA_OPEN_TOP_TIMESTAMP', 'gmt8')
        if self.top_timestamp_mode not in ('gmt8', 'milliseconds'):
            raise OpenError('订单时间戳模式不正确，请管理员核对接口约定')
        self.before_request = before_request

    def authorization_url(self, state=None):
        app = self.credentials.load()
        params = dict(response_type='code', client_id=app['app_key'], redirect_uri=self.redirect_uri)
        if state is not None:
            if not isinstance(state, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', state):
                raise OpenError('授权校验码无效，请重新生成授权链接')
            params['state'] = state
        return self.auth_url + '?' + urlencode(params)

    def _request(self, url, params, *, token_operation=False):
        transport = self.transport
        if transport is None:
            if not self.enable_network:
                raise OpenError('外部接口尚未启用，请管理员核对权限和协议后开启')
            transport = http_transport
        if self.before_request:
            self.before_request()
        try:
            status, payload = transport('POST', url, params, 10)
        except OpenError:
            raise
        except Exception:
            raise OpenError('授权请求结果未知，请重新授权后提交新授权码' if token_operation else
                            '订单接口连接超时或中断，请等待退避结束后重新读取',
                            retryable=not token_operation, unknown=token_operation) from None
        if status == 429 or status >= 500:
            raise OpenError('授权请求结果未知，请重新授权后提交新授权码' if token_operation else
                            '订单接口暂时繁忙，请等待退避结束后重新读取',
                            retryable=not token_operation, unknown=token_operation)
        if not 200 <= status < 300:
            raise OpenError('授权已失效，请重新授权' if token_operation or status in (401, 403) else
                            '订单接口拒绝请求，请管理员核对接口权限和协议',
                            reauthorize=token_operation or status in (401, 403))
        if not isinstance(payload, dict):
            raise OpenError('接口返回格式不正确，请管理员核对接口设置', unknown=token_operation)
        error = payload.get('error_response')
        if error or str(payload.get('code', '0')) != '0':
            code = str(error.get('code', '')) if isinstance(error, dict) else str(payload.get('code', ''))
            reauthorize = token_operation or code in ('25', '26', '27', 'InvalidAccessToken', 'IllegalAccessToken')
            retryable = not token_operation and code in ('7', '15', 'isp.http-read-timeout', 'AppCallLimit')
            raise OpenError('授权已失效，请重新授权' if reauthorize else
                            '订单接口暂时繁忙，请等待退避结束后重新读取' if retryable else
                            '订单接口拒绝请求，请管理员核对接口权限和协议',
                            reauthorize=reauthorize, retryable=retryable)
        return payload

    def _gop(self, path, fields):
        app = self.credentials.load()
        params = dict(app_key=app['app_key'], timestamp=str(int(self.clock() * 1000)),
                      sign_method='sha256', **fields)
        params['sign'] = gop_sign(path, params, app['app_secret'])
        result = self._request(self.gop_url + path, params, token_operation=True)
        try:
            access = _text(result.get('access_token'), 8192)
            refresh = result.get('refresh_token', '')
            if not isinstance(refresh, str):
                raise ValueError
            if refresh:
                _text(refresh, 8192)
            expires = _integer(result.get('expires_in'), 1)
            refresh_expires = _integer(result.get('refresh_expires_in', 0))
            if refresh_expires and not refresh:
                raise ValueError
        except (OpenError, ValueError):
            raise OpenError('授权返回内容不完整，请重新授权后提交新授权码', unknown=True) from None
        return dict(access_token=access, refresh_token=refresh, expires_in=expires,
                    refresh_expires_in=refresh_expires)

    def exchange(self, code):
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,2048}', code):
            raise OpenError('请只粘贴地址栏中 code 的值，不要粘贴整个地址')
        return self._gop('/auth/token/create', dict(code=code))

    def refresh(self, refresh_token):
        _text(refresh_token, 8192)
        return self._gop('/auth/token/refresh', dict(refresh_token=refresh_token))

    def top_parameters(self, method, access_token, fields):
        if method not in READ_METHODS:
            raise OpenError('这里只允许读取订单列表和订单详情')
        app = self.credentials.load()
        stamp = str(int(self.clock() * 1000)) if self.top_timestamp_mode == 'milliseconds' else datetime.fromtimestamp(
            self.clock(), timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        params = dict(method=method, app_key=app['app_key'], session=access_token,
                      timestamp=stamp, sign_method='hmac', format='json', v='2.0', **fields)
        params['sign'] = top_sign(params, app['app_secret'])
        return params

    def orders(self, access_token, watermark_ms, until_ms, page, page_size):
        watermark_ms, until_ms = _integer(watermark_ms), _integer(until_ms)
        page, page_size = _integer(page), _integer(page_size, 1)
        if page_size > 100 or until_ms < watermark_ms:
            raise OpenError('订单读取范围不正确，请管理员检查轮询设置')
        query = dict(role='seller', start_page=page, page_size=page_size,
                     modified_date_start={'time': watermark_ms}, modified_date_end={'time': until_ms})
        fields = dict(param_trade_ecology_order_list_query=json.dumps(query, separators=(',', ':')))
        payload = self._request(self.top_url, self.top_parameters(READ_METHODS[0], access_token, fields))
        try:
            result = payload['alibaba_seller_order_list_response']['result']
            if result['success'] is not True:
                raise ValueError
            value = result['value']
            rows = value.get('order_list', {})
            rows = rows.get('trade_ecology_order', []) if isinstance(rows, dict) else rows
            total = _integer(value['total_count'])
            expected = min(page_size, max(0, total - page * page_size))
            if not isinstance(rows, list) or len(rows) != expected:
                raise ValueError
            orders = [dict(order_id=_text(row['trade_id']),
                           modified_at_ms=_integer(row['modify_date']['timestamp'])) for row in rows]
            if len({row['order_id'] for row in orders}) != len(orders):
                raise ValueError
        except (KeyError, TypeError, ValueError, OpenError):
            raise OpenError('订单列表字段不完整，水位没有推进，请管理员核对字段约定') from None
        return dict(orders=orders, has_more=(page + 1) * page_size < total)

    def order(self, access_token, order_id):
        fields = dict(e_trade_id=_text(order_id), language='zh_CN')
        payload = self._request(self.top_url, self.top_parameters(READ_METHODS[1], access_token, fields))
        try:
            value = payload['alibaba_seller_order_get_response']['value']
            price, buyer = value['total_amount'], value['buyer']
            order = _text(value['trade_id'])
            if order != order_id:
                raise ValueError
            amount, currency = _text(price['amount'], 32), _text(price['currency'], 3)
            if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,2})?', amount) or not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError
            status = _text(value['trade_status'], 64)
            payment = 'paid' if status in ('paid', 'undeliver') else 'unpaid' if status in ('unpay', 'paying') else 'unknown'
            buyer_id = _text(buyer.get('immutable_eid') or buyer.get('e_account_id'))
        except (KeyError, TypeError, ValueError, OpenError):
            raise OpenError('订单详情金额、币种或身份不完整，水位没有推进，请管理员核对字段约定') from None
        return dict(order_id=order, amount=amount, currency=currency, buyer_id=buyer_id,
                    payment_status=payment, paid_at_ms=None, modified_at_ms=None)
