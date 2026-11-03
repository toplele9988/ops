#!/usr/bin/env python3
"""Build public code patches from the observed production and reviewed private candidate.

No private database is read. This is a bundle builder, never an installer.
"""
import difflib
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LIVE = Path('/opt/imgw/wechat')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def build():
    spec = importlib.util.spec_from_file_location('wechat_dossier_baseline', REPO / 'deploy/wechat-private/prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='dossier-build-') as temp:
        # The companion package may have been installed since this task began.
        # Accept only its exact full manifests, never an unverified approximation.
        try:
            module.verify(LIVE, 'baseline.sha256')
        except ValueError:
            module.verify(LIVE, 'candidate.sha256')
            private = LIVE
        else:
            private = module.prepare(LIVE, Path(temp) / 'wechat')
        own_queue = (REPO / 'src/console/ops_queue/service.py').read_text()
        private_queue = (private / 'console/ops_queue/service.py').read_text()
        # The only edits relative to the reviewed private package must be ours.
        own_diff = ''.join(difflib.unified_diff(private_queue.splitlines(True), own_queue.splitlines(True), fromfile='a/console/ops_queue/service.py', tofile='b/console/ops_queue/service.py'))
        (Path(temp) / 'queue.patch').write_text(own_diff)
        raw_queue_root = Path(temp) / 'raw'
        (raw_queue_root / 'console/ops_queue').mkdir(parents=True)
        raw_queue_path = raw_queue_root / 'console/ops_queue/service.py'
        raw_queue_path.write_text(private_queue)
        import subprocess
        companion = (REPO / 'deploy/wechat-private/production.patch').read_text().splitlines(True)
        start = next(i for i, line in enumerate(companion) if line == '--- a/console/ops_queue/service.py\n')
        end = next((i for i in range(start+1,len(companion)) if companion[i].startswith('--- a/')),len(companion))
        reverse = Path(temp) / 'companion-queue.patch'
        reverse.write_text(''.join(companion[start:end]))
        subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-i', str(reverse)], cwd=raw_queue_root, check=True, capture_output=True)
        original_queue = raw_queue_path.read_text()
        assert digest(original_queue.encode()) == module.manifest('baseline.sha256')['console/ops_queue/service.py']
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(Path(temp) / 'queue.patch')], cwd=raw_queue_root, check=True, capture_output=True)
        mount = (LIVE / 'console/v3.py').read_text()
        marker = '    routes += v3_app.build(app_module)'
        assert mount.count(marker) == 1
        mounted = mount.replace(marker, '    from . import v3_dossiers_api\n    routes += v3_dossiers_api.build(app_module)\n' + marker)
        new = {'console/v3_dossiers_api.py': (REPO / 'src/console/v3_dossiers_api.py').read_bytes()}
        new.update({'console/dossiers/' + p.name: p.read_bytes() for p in sorted((REPO / 'src/console/dossiers').glob('*.py'))})
        for variant, queue_base, queue_after in [('production', private_queue, own_queue), ('original', original_queue, (raw_queue_root / 'console/ops_queue/service.py').read_text())]:
            before = {'console/v3.py': mount.encode(), 'console/ops_queue/service.py': queue_base.encode()}
            after = dict(new, **{'console/v3.py': mounted.encode(), 'console/ops_queue/service.py': queue_after.encode()})
            changes = []
            for name, raw in sorted(after.items()):
                changes.extend(difflib.unified_diff(before.get(name, b'').decode().splitlines(True), raw.decode().splitlines(True), fromfile='a/' + name if name in before else '/dev/null', tofile='b/' + name))
            (HERE / (variant + '.patch')).write_text(''.join(changes))
            (HERE / (variant + '.baseline.sha256')).write_text(''.join(f'{digest(raw)}  {name}\n' for name, raw in sorted(before.items())))
            (HERE / (variant + '.candidate.sha256')).write_text(''.join(f'{digest(raw)}  {name}\n' for name, raw in sorted(after.items())))
        (HERE / 'absent.list').write_text(''.join(name + '\n' for name in sorted(new)))
        # Ship the exact canonical importer with its standalone imports.
        for name in ('import_candidate.py', 'prepare.py', 'schema.sql'):
            shutil.copyfile(REPO / 'tools/dossier' / name, HERE / 'importer' / name)
        (HERE / 'importer.sha256').write_text(''.join(f'{digest((HERE / "importer" / name).read_bytes())}  importer/{name}\n' for name in ('import_candidate.py', 'prepare.py', 'schema.sql')))


if __name__ == '__main__':
    build()
