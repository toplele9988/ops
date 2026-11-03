"""Customers grouped by channel/account/buyer_name, not verified identities."""
from starlette.routing import Route

from .customers import public_customer
from .inbox.http import get_handler
from .inbox.model import decode_key, matches, paginate, parameters
from .inbox.store import INBOUND_PATH
from .channels import build_inbox


def build(app_module, inbound_path=INBOUND_PATH, *, inbox=None, wechat_path=None,
          whatsapp_config_path=None, fetch=None, clock=None, stat_fn=None, group_gid=None):
    cache = inbox if inbox is not None else build_inbox(inbound_path, wechat_path=wechat_path,
        whatsapp_config_path=whatsapp_config_path, fetch=fetch, clock=clock, stat_fn=stat_fn, group_gid=group_gid)

    def customers(values):
        snapshot = cache.read()
        rows = [c for c in snapshot['customers'].values() if matches(c, values)]
        rows.sort(key=lambda c: (-c['last_seen'], c['key']))
        return paginate([public_customer(c) for c in rows], values, snapshot['ready']), 200

    def key_parameter(request):
        key = request.path_params.get('key', '')
        decode_key(key, 'customer')
        return key

    def detail(key):
        customer = cache.read()['customers'].get(key)
        if customer is None:
            return {'error': '未找到客户'}, 404
        return public_customer(customer, detail=True), 200

    def get(parse, read):
        return get_handler(app_module, parse, read, '客户资料暂时无法读取')

    return [Route('/api/v1/customers', get(lambda r: parameters(r.query_params), customers),
                  methods=['GET'], name='customers_list'),
            Route('/api/v1/customers/{key}', get(key_parameter, detail), methods=['GET'], name='customers_detail')]
