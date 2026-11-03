"""Shared reply direction and availability; never reproduce chat bodies."""
from datetime import datetime, timezone
import math

from console.inbox.model import is_closing

OVERDUE_HOURS = 48


def last_known(conversation):
    return next((m for m in reversed(conversation.get('messages') or ())
                 if m.get('role') in ('buyer', 'seller')), None)


def observation(conversation):
    """One direction contract for deals and roles; unknown observations keep the clock."""
    if not conversation:
        return None
    messages = conversation.get('messages') or ()
    last = last_known(conversation)
    if last is None:
        return dict(last_role='unknown', last_at=None, availability='unknown',
                    channel_label=conversation.get('channel_label'), title=conversation.get('buyer_name'))
    closed = is_closing(last.get('text', ''))
    since = seconds(last.get('sent_at'))
    # Consecutive outgoing messages may be reminders. A reminder is not a new
    # reply from the other party and must not make the original wait look young.
    if last['role'] == 'seller' and not closed:
        for message in reversed(messages):
            if message.get('role') == 'buyer':
                break
            if message.get('role') == 'seller' and not is_closing(message.get('text', '')):
                at = seconds(message.get('sent_at'))
                if at is not None:
                    since = at
    unknown = bool(messages and messages[-1].get('role') not in ('buyer', 'seller'))
    return dict(last_role='closed' if closed else last['role'], last_at=seconds(last.get('sent_at')),
                since=since, last_reminded_at=seconds(last.get('sent_at')) if since != seconds(last.get('sent_at')) else None,
                availability='unknown' if unknown else 'readable', channel_label=conversation.get('channel_label'),
                title=conversation.get('buyer_name'))


def seconds(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, OverflowError):
        return None


def due_task(task, now):
    due = seconds(task.get('due'))
    return task.get('status') == 'open' and due is not None and due <= now


def evaluate(participants, conversations, tasks, lookup, now, overdue_hours=OVERDUE_HOURS):
    projected, convs = [], []
    for conv in conversations:
        key = conv['conversation_key']
        try:
            data = lookup(key) or {}
        except Exception:
            data = {}
        at = seconds(data.get('last_at'))
        availability = data.get('availability') or ('readable' if data.get('last_role') in ('buyer', 'seller', 'closed') else 'unknown' if data else 'unreadable')
        state = {'buyer': 'us', 'seller': 'them'}.get(data.get('last_role'), 'none')
        convs.append(dict(id=conv['id'], key=key, party_id=conv.get('party_id'), note=conv.get('note'),
                          channel_label=data.get('channel_label'), title=data.get('title'),
                          last_role=data.get('last_role'), last_at=at, state=state, availability=availability,
                          since=seconds(data.get('since')) if data.get('since') is not None else at,
                          last_reminded_at=seconds(data.get('last_reminded_at'))))
    waiting_on, overdue = [], []
    unlinked = 0
    for participant in participants:
        links = [c for c in convs if c['party_id'] == participant['party_id']]
        latest = max((c for c in links if c['last_at'] is not None), key=lambda c: c['last_at'], default=None)
        state = latest['state'] if latest else 'none'
        availability = ('unlinked' if not links else 'unreadable' if any(c['availability'] == 'unreadable' for c in links)
                        else 'unknown' if any(c['availability'] == 'unknown' for c in links) else 'readable')
        projected.append(dict(participant, state=state, last_at=latest['last_at'] if latest else None,
                              availability=availability, conversations=links))
        unlinked += not links
        pending = [c for c in links if c['state'] == 'them']
        if pending:
            if participant['role'] not in waiting_on:
                waiting_on.append(participant['role'])
            if any(c['since'] is not None and now - c['since'] > overdue_hours * 3600 for c in pending) and participant['role'] not in overdue:
                overdue.append(participant['role'])
    waiting_on_us = any(c['state'] == 'us' for c in convs)
    due_tasks = sum(due_task(t, now) for t in tasks)
    return dict(participants=projected, conversations=convs, waiting_on_us=waiting_on_us,
                waiting_on=waiting_on, overdue=overdue, unlinked_participants=unlinked,
                due_tasks=due_tasks, needs_attention=bool(waiting_on_us or overdue or due_tasks))


def inbox_lookup(inbox, now):
    """Request-local snapshot; merged inbox and adapter caches remain process-shared.

    Listing already contains the last message (including WhatsApp overview), so
    this does not fetch per-chat histories. An unavailable snapshot means no data.
    """
    snapshot = None
    def lookup(key):
        nonlocal snapshot
        if snapshot is None:
            try:
                snapshot = inbox.read(now)['conversations']
            except Exception:
                snapshot = {}
        conv = snapshot.get(key)
        if not conv:
            return None
        return observation(conv)
    return lookup
