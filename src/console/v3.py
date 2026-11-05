from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse
from starlette.routing import Route

from . import v3_B, v3_C, v3_operations, v3_owner_inbox, v3_admin


def build(app_module):
    async def redirect(request):
        return RedirectResponse('/app/', status_code=302)

    async def overview(request):
        result = await run_in_threadpool(
            app_module.safe_panel, 'v3.overview', app_module.load_page, 'overview')
        panels = result['data'] if result['ok'] else {
            name: {'ok': False, 'incident': result.get('incident')}
            for name in ('stats', 'models', 'services', 'todo')}
        # New sources remain visible if a legacy panel's loader fails.
        return app_module.render(
            request, 'v3/overview.html', nav='overview',
            panels=panels, trend_points=[],
            operations=await run_in_threadpool(app_module.safe_panel,
                'operations.overview', v3_operations.home_data, app_module))

    routes = [
        Route('/v3', redirect, methods=['GET'], name='v3_redirect'),
        Route('/v3/', redirect, methods=['GET'], name='v3_overview'),
    ]
    routes += v3_B.build(app_module)
    routes += v3_C.build(app_module)
    routes += v3_operations.build(app_module)
    routes += v3_owner_inbox.build(app_module)
    from . import v3_wechat_knowledge
    routes += v3_wechat_knowledge.build(app_module)
    from . import v3_supplier_packing
    routes += v3_supplier_packing.build(app_module)
    from . import v3_app, v3_inspiration_api, v3_packing_api, v3_inbox_api, v3_customers_api, v3_channels_api, v3_outbox_api, v3_media_api, v3_kb_api, v3_archive_api, v3_deals_api, v3_quotes_api, v3_roles_api, v3_whatsapp_login_api, v3_directory_api, v3_audit_api, v3_extract_api, v3_fulfil_api
    routes += v3_inspiration_api.build(app_module)
    routes += v3_packing_api.build(app_module)
    routes += v3_inbox_api.build(app_module)
    routes += v3_customers_api.build(app_module)
    routes += v3_channels_api.build(app_module)
    routes += v3_outbox_api.build(app_module)
    routes += v3_media_api.build(app_module)
    routes += v3_kb_api.build(app_module)
    routes += v3_archive_api.build(app_module)
    routes += v3_deals_api.build(app_module)
    routes += v3_quotes_api.build(app_module)
    routes += v3_roles_api.build(app_module)
    from .operator import host as operator_host
    routes += operator_host.build(app_module)
    routes += v3_whatsapp_login_api.build(app_module)
    routes += v3_directory_api.build(app_module)
    from . import v3_profiles_api
    routes += v3_profiles_api.build(app_module)
    from . import v3_classify_api
    routes += v3_classify_api.build(app_module)
    routes += v3_audit_api.build(app_module)
    routes += v3_extract_api.build(app_module)
    routes += v3_fulfil_api.build(app_module)
    from . import v3_lang_api, v3_pipeline_api
    routes += v3_lang_api.build(app_module)
    routes += v3_pipeline_api.build(app_module)
    from . import v3_overview_api
    routes += v3_overview_api.build(app_module)
    from . import v3_alibaba_open_api
    routes += v3_alibaba_open_api.build(app_module, network_enabled=False)
    from . import v3_deal_workflow_api
    routes += v3_deal_workflow_api.build(app_module)
    from . import v3_ops_r11_api
    routes += v3_ops_r11_api.build(app_module)
    from . import v3_dossiers_api
    routes += v3_dossiers_api.build(app_module)
    routes += v3_app.build(app_module)
    routes += v3_admin.build(app_module)
    return routes
