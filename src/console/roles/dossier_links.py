"""Read-only dossier group evidence; never initialize or migrate the side store."""
import json
from pathlib import Path
import re
import sqlite3

from console.dossiers import store as dossiers
from console.inbox.model import make_key

KINDS = {'产品': 'supplier', '物流': 'forwarder', '配套': 'vendor'}


def json_list(value):
    try:
        result = json.loads(value or '[]')
    except (ValueError, TypeError):
        return []
    return result if isinstance(result, list) else []


def names(*values):
    return {v.strip().casefold() for v in values if isinstance(v, str) and v.strip()}


def load(path=None, *, archive_path):
    """Return one link per supplier/group, retaining duplicates across suppliers.

    A missing legacy map only removes that matching tier. A missing supplier
    table or unavailable snapshot is an empty optional source.
    """
    links = []
    try:
        with dossiers.readonly(dossiers.DB_PATH if path is None else path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'sd_supplier' not in tables:
                return []
            mappings = {}
            if 'sd_legacy_map' in tables:
                for row in db.execute("SELECT * FROM sd_legacy_map WHERE legacy_table='parties' AND entity_kind='supplier'"):
                    # Never borrow a coincident row ID from another legacy store.
                    source = str(row['legacy_store']).strip()
                    if source not in ('archive', 'archive.sqlite3', str(Path(archive_path)),
                                      str(Path(archive_path).absolute())):
                        continue
                    if str(row['state']).strip().casefold() in (
                            'conflict', 'ambiguous', 'rejected', 'unmapped', 'ignored', 'pending'):
                        continue
                    if row['entity_id'] is not None:
                        mappings.setdefault(row['entity_id'], set()).add(str(row['legacy_key']))
            for row in db.execute('SELECT supplier_id,name,supplier_type,aliases_json,contacts_json FROM sd_supplier ORDER BY supplier_id'):
                kind = KINDS.get(row['supplier_type'])
                if not kind or not isinstance(row['name'], str) or not row['name'].strip():
                    continue
                groups = {}
                for contact in json_list(row['contacts_json']):
                    if not isinstance(contact, dict) or contact.get('channel') != 'wechat_group':
                        continue
                    group = contact.get('group_id')
                    if not isinstance(group, str) or not re.fullmatch(r'[0-9]+@chatroom', group.strip()):
                        continue
                    groups.setdefault(group.strip(), []).append(contact.get('source_id'))
                for group, sources in groups.items():
                    links.append(dict(conversation_key=make_key('conversation', 'wechat', '微信', group),
                        supplier_id=row['supplier_id'], name=row['name'].strip(),
                        aliases=[a for a in json_list(row['aliases_json']) if isinstance(a, str)],
                        source_id='、'.join(sorted({s for s in sources if isinstance(s, str) and s.strip()})),
                        kind=kind, party_ids=sorted(mappings.get(row['supplier_id'], ()))))
    except (OSError, ValueError, sqlite3.Error):
        return []
    return links


def index(links):
    result = {}
    for link in links:
        result.setdefault(link['conversation_key'], []).append(link)
    return result


def match(links, parties):
    """A legacy mapping takes precedence; ambiguity never picks an arbitrary row."""
    if len({link['supplier_id'] for link in links}) > 1:
        return None, None, '同一群号对应多个档案供应商，请人工核对'
    if not links:
        return None, None, None
    link = links[0]
    active = [p for p in parties if p['status'] == 'active']
    matches = [p for p in active if str(p['id']) in link['party_ids']]
    if not matches:
        supplier_names = names(link['name'], *link['aliases'])
        matches = [p for p in active if supplier_names & names(
            p['name'], p.get('short_name'), *json_list(p.get('aliases_json')))]
    if len(matches) > 1:
        return None, None, '档案供应商对应多个有效往来单位，请人工核对'
    return link, matches[0] if matches else None, None
