import json
from pathlib import Path
import shlex
import sqlite3
import subprocess

import pytest

from conftest import ROOT, load


def payload(**changes):
    proof = dict(schema='amy-live-v1', mode='dom_tail_append',
        epoch_uuid='11111111-1111-4111-8111-111111111111', observed_seq=1,
        baseline_server_time_utc='2026-11-01T00:00:00Z', baseline_watermark_sent_at='2026-11-01T00:00:00Z')
    proof.update(changes)
    return dict(live_provenance=proof, history=False, role='buyer',
        sent_at='2026-11-01T00:00:05Z', observed_at='2026-11-01T00:00:06Z')


@pytest.mark.parametrize('change', [dict(epoch_uuid='fictional-invalid'), dict(observed_seq=True),
    dict(observed_seq=0), dict(observed_seq='FICTIONAL_SENTINEL'),
    dict(baseline_server_time_utc='2026-11-01T00:00:00'),
    dict(baseline_watermark_sent_at='2026-11-01T00:01:00Z')])
def test_invalid_proof_cannot_be_reported_ok(candidate, change):
    before = load('readback_before', ROOT / 'scripts/icbu_live_readback.py')
    after = load('readback_after', candidate / 'scripts/icbu_live_readback.py')
    assert before.proof_status(payload(**change)) == 'ok'
    assert after.proof_status(payload(**change)) == 'malformed'


def test_both_receiver_modes_and_empty_evidence(candidate, capsys):
    module = load('readback_modes', candidate / 'scripts/icbu_live_readback.py')
    for mode in ('dom_tail_append', 'native_poll_after_baseline'):
        assert module.proof_status(payload(mode=mode)) == 'ok'
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE inbound(id,payload,state,archive_reason,created_at)')
        module.readback(db, 0, True)
    assert json.loads(capsys.readouterr().out)['summary']['seq_strictly_increasing'] is None


def test_readback_does_not_echo_free_text_metadata(candidate, capsys):
    module = load('readback_redaction', candidate / 'scripts/icbu_live_readback.py')
    value = payload(observed_seq='FICTIONAL_SEQUENCE_SENTINEL')
    value.update(probe_version='FICTIONAL_VERSION_SENTINEL', account='fictional-account',
                 conversation_id='fictional-conversation', message_id='1')
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE inbound(id,payload,state,archive_reason,created_at)')
        db.execute('INSERT INTO inbound VALUES(?,?,?,?,?)', ('11111111-1111-4111-8111-111111111111',
            json.dumps(value), 'FICTIONAL_STATE_SENTINEL', 'FICTIONAL_REASON_SENTINEL', module.parse('2026-11-01T00:00:07Z')))
        module.readback(db, 0, True)
    result = capsys.readouterr().out
    assert 'SENTINEL' not in result and json.loads(result)['summary']['proof_ok'] == 0


def test_nas_probe_preserves_sentinel_and_existing_files(candidate, tmp_path):
    source = (candidate / 'scripts/nas_imgw_archive_mount.sh').read_text()
    # Execute only the byte-probe block, never root checks, mount or systemctl.
    block = source.split("<<'VERIFY_ARCHIVE'\n", 1)[1].split('\nVERIFY_ARCHIVE', 1)[0]
    root = tmp_path / 'fictional directory with spaces'
    root.mkdir()
    sentinel = root / '.imgw-archive-id'
    sentinel.write_bytes(b'fictional original sentinel')
    identity = sentinel.stat().st_ino
    victim = tmp_path / 'fictional-keep.txt'
    victim.write_bytes(b'fictional original bytes')
    (root / '.t.tmp').symlink_to(victim)
    (root / '.t.bin').write_bytes(b'fictional existing file')
    for _ in range(2):
        result = subprocess.run(['sh', '-es', '--', str(root)], input=block, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    assert sentinel.stat().st_ino == identity and sentinel.read_bytes() == b'fictional original sentinel'
    assert victim.read_bytes() == b'fictional original bytes'
    assert (root / '.t.bin').read_bytes() == b'fictional existing file'
    assert not list(root.glob('.imgw-check.*'))
    sentinel.unlink()
    sentinel.symlink_to(victim)
    result = subprocess.run(['sh', '-es', '--', str(root)], input=block, text=True, capture_output=True)
    assert result.returncode != 0 and victim.read_bytes() == b'fictional original bytes'


def test_original_nas_probe_overwrites_predictable_name(tmp_path):
    # The original fixed-name write is reproduced solely against a fictional link.
    root = tmp_path / 'nas'
    root.mkdir()
    victim = tmp_path / 'keep.txt'
    victim.write_bytes(b'fictional original bytes')
    (root / '.t.tmp').symlink_to(victim)
    command = 'D=' + shlex.quote(str(root)) + '\nhead -c 16 /dev/zero > $D/.t.tmp'
    subprocess.run(['sh', '-ec', command], check=True)
    assert victim.read_bytes() == bytes(16)
