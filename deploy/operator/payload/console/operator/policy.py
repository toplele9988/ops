"""Fail-closed level/type/contact gates, recipient-local hours and rolling quotas."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LEVELS = {'ask_supplier': 1, 'ask_forwarder': 1, 'reply_customer': 2,
          'quote_customer': 3, 'draft_pi': 3, 'draft_order': 3, 'shipping': 3,
          'create_deal': 3, 'advance_deal': 3, 'update_profile': 3, 'ask_owner': 0,
          'schedule_shipping': 3, 'confirm_payment': 99}
INTERNAL = frozenset(('create_deal', 'advance_deal', 'update_profile', 'ask_owner', 'schedule_shipping'))
TERMINAL = frozenset(('sent', 'done', 'rejected', 'unknown', 'failed', 'duplicate'))


def daytime(now, profile):
    try:
        zone = ZoneInfo(profile['timezone'])
    except (KeyError, ValueError, ZoneInfoNotFoundError, TypeError):
        return None
    local = datetime.fromtimestamp(now, zone)
    if 8 <= local.hour < 20:
        return now
    morning = (local + timedelta(days=local.hour >= 20)).date()
    return datetime.combine(morning, datetime.min.time().replace(hour=8), zone).astimezone(timezone.utc).timestamp()


def evaluate(settings, action, profile, now, *, approved=False):
    kind, level = action['kind'], LEVELS[action['kind']]
    if kind == 'confirm_payment':
        return 'blocked', 'payment_always_manual', None
    if kind == 'ask_owner':
        return 'pending', 'owner_required', None
    if not settings['enabled'] or settings['dry_run']:
        return 'dry_run', 'master_off' if not settings['enabled'] else 'rehearsal', None
    if not approved:
        allowed = (settings['levels'].get(str(level)) is True
                   and settings['types'].get(f"{level}:{profile.get('type', '')}") is True
                   and settings['contacts'].get(f"{level}:{action.get('contact_key', '')}") is True)
        # L1 must never be usable for ordering or customer messages.
        if level == 1 and profile.get('type', '').split('_')[0] != 'supplier':
            allowed = False
        if not allowed:
            return 'pending', 'switch_off', None
    if kind in INTERNAL:
        return 'ready', 'approved' if approved else 'authorized', now
    if profile.get('channel') not in ('wechat', 'whatsapp'):
        return 'pending', 'channel_unavailable', None
    due = daytime(now, profile)
    if due is None:
        return 'pending', 'timezone_required', None
    if due > now:
        return 'scheduled', 'recipient_night', due
    return 'ready', 'approved' if approved else 'authorized', now


def pace(db, contact_key, profile, now):
    waits = []
    # Windows count every attempted segment, including uncertain and refused attempts.
    for seconds, limit in ((3600, profile.get('hourly_limit', 6)), (86400, profile.get('daily_limit', 20))):
        times = [r[0] for r in db.execute('SELECT created_at FROM operator_attempts '
                'WHERE contact_key=? AND created_at>? ORDER BY created_at', (contact_key, now-seconds))]
        if len(times) >= limit:
            waits.append(times[len(times)-limit] + seconds + .001)
    last = db.execute('SELECT max(created_at) FROM operator_attempts WHERE contact_key=?', (contact_key,)).fetchone()[0]
    if last is not None and now < last + profile.get('interval_seconds', 30):
        waits.append(last + profile.get('interval_seconds', 30))
    return max(waits) if waits else now
