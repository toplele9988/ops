"""Offline display-name resolution; only NamesRefresh makes metadata requests."""
from .country import country_from_phone
from .resolver import NamesResolver


def group_name(group_id, **kwargs):
    return NamesResolver().group_name(group_id, **kwargs)


def member_name(group_id, sender_id):
    return NamesResolver().member_name(group_id, sender_id)


def contact_name(identity, **kwargs):
    return NamesResolver().contact_name(identity, **kwargs)


def __getattr__(name):
    if name == 'NamesRefresh':
        from .refresh import NamesRefresh
        return NamesRefresh
    raise AttributeError(name)


__all__ = ['NamesResolver', 'NamesRefresh', 'group_name', 'member_name', 'contact_name', 'country_from_phone']
