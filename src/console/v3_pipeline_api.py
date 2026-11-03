"""Authenticated, metadata-only pipeline health. No collection or write route."""
from starlette.routing import Route

from .inbox.http import get_handler
from .inbox.model import BadRequest
from .pipeline import PipelineHealth


def build(app_module, *, health=None, **options):
    service = health if health is not None else PipelineHealth(**options)

    def parse(request):
        if request.query_params:
            raise BadRequest('管道健康页不接受查询参数，请直接刷新页面')

    def read(_):
        return service.read(), 200

    return [Route('/api/v1/pipeline/health', get_handler(app_module, parse, read,
                  '管道健康状态暂时读不到，请稍后刷新；若持续出现，请管理员检查服务'),
                  methods=['GET'], name='pipeline_health')]
