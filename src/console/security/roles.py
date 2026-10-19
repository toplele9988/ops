from starlette.requests import Request

ALL_PERMISSIONS = (
    'strobe.view', 'cpq.quote', 'cpq.view_cost',
    'cpq.override_margin', 'cpq.view_supplier', 'cpq.admin',
)
ROLE_PERMISSIONS = {
    'viewer': frozenset({'strobe.view'}),
    'sales': frozenset({'strobe.view', 'cpq.quote'}),
    'sales_cost': frozenset({'strobe.view', 'cpq.quote', 'cpq.view_cost'}),
    'manager': frozenset({'strobe.view', 'cpq.quote', 'cpq.view_cost', 'cpq.override_margin'}),
    'admin': frozenset(ALL_PERMISSIONS),
}


def permissions_for(request: Request, testing: bool) -> frozenset[str] | None:
    if testing and ('x-test-user' in request.headers or 'x-test-roles' in request.headers):
        roles = {part.strip().lower() for part in request.headers.get('x-test-roles', '').split(',')}
        return frozenset().union(*(ROLE_PERMISSIONS.get(role, frozenset()) for role in roles))
    session = getattr(request.state, 'session', None)
    if getattr(session, 'authenticated', False) or getattr(request.state, 'proxy_trusted', False):
        return frozenset(ALL_PERMISSIONS)
    return None
