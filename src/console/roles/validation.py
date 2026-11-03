"""Strict form/query whitelists; errors are fixed Chinese strings."""
from console.archive import validation as v
from console.deals.validation import identity
from .common import ROLE_INFO

FIELDS = {
    'secretary': (), 'triage': (), 'patrol': (), 'brief': (),
    'alert_dismiss': (), 'triage_dismiss': (),
    'triage_accept': ('action', 'deal_id', 'party_id', 'party_kind', 'party_name', 'title'),
}


def fields(operation, raw):
    if operation not in FIELDS:
        raise v.BadRequest('岗位操作无效')
    v.whitelist(raw, FIELDS[operation])
    result = {}
    for name, value in raw.items():
        if name in ('deal_id', 'party_id'):
            result[name] = identity(value)
        elif name == 'action':
            result[name] = v.choice(value, ('link', 'create', 'identity', 'ignore'))
        elif name == 'party_kind':
            result[name] = v.choice(value, v.PARTY_KINDS)
        elif name == 'party_name':
            result[name] = v.party_name(value)
        else:
            result[name] = v.text(value, 500, required=True)
    return result


def parameters(params, resource):
    allowed = {'runs': ('role', 'limit'), 'triage': ('status',), 'alerts': ('deal_id', 'include_dismissed')}.get(resource, ())
    v.whitelist(params, allowed)
    result = {}
    for name, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(name)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        if not isinstance(value, str):
            raise v.BadRequest('请求参数须为文本')
        if name == 'limit':
            result[name] = v.integer(value, maximum=200)
        elif name == 'deal_id' and value:
            result[name] = identity(value)
        elif name == 'include_dismissed':
            result[name] = v.choice(value, ('', '0', '1')) == '1'
        elif name == 'role' and value:
            result[name] = v.choice(value, tuple(ROLE_INFO))
        elif name == 'status':
            result[name] = v.choice(value, ('pending', 'accepted', 'dismissed', 'all'))
    return result
