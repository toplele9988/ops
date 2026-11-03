"""Shared, read-only canonical party projection. Historical FKs stay untouched."""

def merge_map(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='parties_ops_merges'").fetchone():
        return {}
    return {r['source_id']: r['target_id'] for r in db.execute(
        'SELECT source_id,target_id FROM parties_ops_merges WHERE undone_at IS NULL')}


def canonical(db, party_id):
    return merge_map(db).get(party_id, party_id)


def projected_parties(db):
    parties = {r['id']: dict(r) for r in db.execute('SELECT * FROM parties')}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='parties_ops_profiles'").fetchone():
        for row in db.execute('SELECT * FROM parties_ops_profiles'):
            if row['party_id'] in parties:
                parties[row['party_id']]['timezone'] = dict(row).get('timezone') or None
    for source, target in merge_map(db).items():
        if target in parties:
            parties[source] = parties[target]
    return parties


def bindings(db):
    parties = projected_parties(db)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identities'").fetchone():
        return {}
    return {r['conversation_key']: parties[r['party_id']] for r in db.execute('SELECT * FROM identities')
            if r['party_id'] in parties}
