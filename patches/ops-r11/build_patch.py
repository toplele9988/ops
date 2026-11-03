"""Generate a reviewed r11 patch against the actual read-only production tree.

Only public Python source is included. Private diff/audit material goes private_out.
"""
from pathlib import Path
import hashlib
import difflib
import json

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parent
PRODUCTION = Path('/opt/imgw/wechat')
names = set()
for manifest in ('inbox/patches/CANDIDATE.sha256', 'deals/patches/candidate.sha256', 'customers/patches/candidate.sha256'):
    names.update(line.split()[1] for line in (ROOT / 'src/console' / manifest).read_text().splitlines())
names.update('console/' + name for name in ('customers/identity.py', 'archive/store.py', 'inbox/model.py', 'v3_ops_queue_api.py', 'v3_ops_drafts_api.py', 'v3_ops_r11_api.py'))
names.update('console/ops_queue/' + p.name for p in (ROOT / 'src/console/ops_queue').glob('*.py'))
names.add('console/v3.py')
base, candidate, absent, changes = [], [], [], []
audit = []
for name in sorted(names):
    path = PRODUCTION / name
    before = path.read_bytes() if path.exists() else b''
    if name == 'console/v3.py':
        after = before.decode()
        marker = '    routes += v3_app.build(app_module)'
        assert after.count(marker) == 1
        after = after.replace(marker, '    from . import v3_ops_r11_api\n    routes += v3_ops_r11_api.build(app_module)\n' + marker).encode()
        (BUNDLE / 'v3.candidate.py').write_bytes(after)
    else:
        after = (ROOT / 'src' / name).read_bytes()
    digest = lambda value: hashlib.sha256(value).hexdigest()
    audit.append(dict(path=name, production=digest(before) if path.exists() else None, candidate=digest(after), changed=before != after))
    if before == after:
        continue
    if path.exists():
        base.append(f'{digest(before)}  {name}\n')
    else:
        absent.append(name + '\n')
    candidate.append(f'{digest(after)}  {name}\n')
    changes.extend(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
        fromfile='a/' + name if path.exists() else '/dev/null', tofile='b/' + name))
(BUNDLE / 'baseline.sha256').write_text(''.join(base))
(BUNDLE / 'candidate.sha256').write_text(''.join(candidate))
(BUNDLE / 'absent.list').write_text(''.join(absent))
(BUNDLE / 'production.patch').write_text(''.join(changes))
(ROOT / 'private_out/r11/source-sha-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
# Rehearsal baseline is this task's workspace snapshot, not the production host.
prior = (BUNDLE / 'rehearsal.baseline.py').read_text().splitlines(True)
current = (ROOT / 'scripts/rehearsal_server.py').read_text().splitlines(True)
(BUNDLE / 'rehearsal.patch').write_text(''.join(difflib.unified_diff(prior, current,
    fromfile='a/scripts/rehearsal_server.py', tofile='b/scripts/rehearsal_server.py')))
print(f'{len(candidate)} files, {len(base)} existing production baselines, {len(absent)} new files')

for manifest, value in [("rehearsal.baseline.sha256", "".join(prior).encode()), ("rehearsal.candidate.sha256", "".join(current).encode())]:
    (BUNDLE / manifest).write_text(hashlib.sha256(value).hexdigest() + "  scripts/rehearsal_server.py\n")
