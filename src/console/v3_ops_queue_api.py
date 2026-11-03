"""Authenticated read-only operations queue. Host authentication remains required."""
from starlette.routing import Route

from .inbox.http import get_handler
from .inbox.model import BadRequest
from .ops_queue import OpsQueue


def build(app_module, *, service=None, **options):
    service = service if service is not None else OpsQueue(**options)

    def parse(request):
        if request.query_params:
            raise BadRequest('今日待办不接受查询参数，请直接刷新')

    return [Route('/api/v1/ops/queue', get_handler(app_module, parse,
        lambda _: (service.read(), 200), '待办暂时读不到，请刷新重试或到对应页面核对'),
        methods=['GET'], name='ops_queue')]
