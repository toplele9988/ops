"""Allow the audit role without rewriting deployed v4/v5 migrations.

Only role_runs' role CHECK changes. Columns, rows, indexes, triggers and child
references are preserved, including additive columns from an installed v5.
"""
import re


def upgrade(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='role_runs'").fetchone()
    if row is None:
        raise ValueError('岗位运行表不存在')
    sql = row[0]
    pattern = r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)\s*\)"
    match = re.search(pattern, sql, re.I)
    if match is None or not re.fullmatch(r"\s*'[a-z_]+'(?:\s*,\s*'[a-z_]+')*\s*", match[1]):
        raise ValueError('岗位运行约束不兼容')
    if 'audit' in re.findall(r"'([a-z_]+)'", match[1]):
        return
    sql = sql[:match.start()] + "CHECK(role IN (" + match[1] + ",'audit'))" + sql[match.end():]
    sql, count = re.subn(r'^CREATE\s+TABLE\s+["`\[]?role_runs["`\]]?',
                        'CREATE TABLE role_runs_audit_v6', sql, count=1, flags=re.I)
    if count != 1:
        raise ValueError('岗位运行结构不兼容')
    objects = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='role_runs' "
               "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    columns = ','.join('"' + r[1].replace('"', '""') + '"' for r in db.execute('PRAGMA table_xinfo(role_runs)') if not r[6])
    db.execute(sql)
    db.execute(f'INSERT INTO role_runs_audit_v6 ({columns}) SELECT {columns} FROM role_runs')
    db.execute('DROP TABLE role_runs')
    legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
    try:
        # Existing views/child references must keep referring to role_runs.
        # Modern ALTER validates views in the brief drop/rename interval.
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('ALTER TABLE role_runs_audit_v6 RENAME TO role_runs')
    finally:
        db.execute('PRAGMA legacy_alter_table=' + ('ON' if legacy else 'OFF'))
    for statement in objects:
        db.execute(statement)


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=6)
