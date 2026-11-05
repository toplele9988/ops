"""Explicit retirement helper. Caller must own a transaction and stop workers first."""
from .project import KEYS


def rollback(db):
    """Remove only journal-owned appended rows, then the additive tables.

    Foreign keys must remain enabled. A later external reference causes the
    caller's transaction to fail instead of deleting unrelated dossier rows.
    This function is never called by migration, run, or a service read.
    """
    if not db.in_transaction or not db.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise ValueError('rollback_requires_foreign_keys_and_transaction')
    db.execute('DELETE FROM sd_gap_fill')
    db.execute('DELETE FROM sd_review')
    removed = {}
    for table in ('sd_evidence', 'sd_offer', 'sd_supplier_product', 'sd_product', 'sd_source', 'sd_supplier'):
        clause = ' AND '.join(table + '.' + key + "=json_extract(p.row_key,'$[" + str(i) + "]')" for i, key in enumerate(KEYS[table]))
        removed[table] = db.execute('DELETE FROM ' + table + " WHERE EXISTS(SELECT 1 FROM sd_projection p WHERE p.table_name=? AND " + clause + ')', (table,)).rowcount
    for table in ('sd_projection', 'sd_gap_fill', 'sd_review', 'sd_run', 'sd_job', 'sd_cursor'):
        db.execute('DROP TABLE ' + table)
    return removed
