"""Authenticated GET/HEAD overview using the existing inbox HTTP gates."""
from starlette.routing import Route

from .inbox.http import get_handler
from .inbox.model import BadRequest
from .overview import Overview


def build(app_module, *, overview=None, **options):
    service = overview if overview is not None else Overview(**options)

    def parse(request):
        if request.query_params:
            raise BadRequest('全局总览不接受查询参数，请直接刷新页面')

    def read(_):
        return service.read(), 200

    return [Route('/api/v1/overview', get_handler(app_module, parse, read,
                  '全局总览暂时读不到，请稍后刷新；若持续出现，请管理员检查服务'),
                  methods=['GET'], name='overview')]
