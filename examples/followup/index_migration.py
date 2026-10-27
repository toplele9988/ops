"""Explicit candidate DDL; never called by a request or run on production here."""
NOTES=(
 'CREATE INDEX followup_status_order ON conversation_notes(status, updated_at DESC, key DESC)',
 'CREATE INDEX followup_all_order ON conversation_notes(updated_at DESC, key DESC)',
)
WECHAT=('CREATE INDEX followup_wechat_source ON messages(app_id, group_id, created_at, message_id)',)
ALIBABA=("CREATE INDEX followup_alibaba_source ON inbound(CASE WHEN json_valid(payload) THEN json_extract(payload, '$.account') END, CASE WHEN json_valid(payload) THEN json_extract(payload, '$.conversation_id') END, state, id)",)
WHATSAPP=('CREATE INDEX followup_whatsapp_source ON messages(session, account_id, owner, scope, chat_id, message_key)',)

def install_for_fixture(db,statements):
    # Only synthetic isolated databases in this candidate. Actual deployment needs
    # separate backup, migration locking, disk/time benchmarks and review.
    with db:
        for sql in statements:db.execute(sql)
