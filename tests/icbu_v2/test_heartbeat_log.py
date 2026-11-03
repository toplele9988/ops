"""Heartbeat log candidate: every accepted heartbeat is one row; retention is 14 days; latest-state behaviour unchanged."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[2] / 'deploy/icbu/heartbeat-log/icbu_probe_heartbeat.py'


def load():
    spec = importlib.util.spec_from_file_location('icbu_probe_heartbeat_log_candidate', CANDIDATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def beat(state='running', error=None, loaded=3):
    return {'schema': 'icbu_probe_heartbeat_v1', 'account': 'Amy',
            'instance_id': '11111111-1111-4111-8111-111111111111', 'version': '0.2.2', 'state': state,
            'range': {'date_start': '2026-10-24', 'date_end': '2026-11-01'},
            'counters': {'contacts_loaded': loaded, 'contacts_scanned': 1, 'unbound': 0,
                         'metadata_skipped': 0, 'queued': 0, 'blocked': 0},
            'last_error': error, 'cycle_complete': False}


class HeartbeatLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.chmod(root, 0o700)
        self.path = root / 'probe.sqlite3'
        self.module = load()
        self.store = self.module.HeartbeatStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT received_at,state,last_error,version,range_end FROM heartbeat_log ORDER BY id').fetchall()

    def test_each_accepted_heartbeat_is_logged_and_latest_state_still_works(self):
        for index in range(3):
            self.store.receive(beat(), now=1_800_000_000 + index * 120)
        self.store.receive(beat('paused', 'browser_disconnected'), now=1_800_000_360)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1][1:], ('paused', 'browser_disconnected', '0.2.2', '2026-11-01'))
        self.assertEqual(self.store.status()['snapshot']['last_error'], 'browser_disconnected')

    def test_rejected_heartbeat_leaves_no_row(self):
        bad = beat()
        bad['extra'] = 1
        with self.assertRaises(self.module.HeartbeatRejected):
            self.store.receive(bad, now=1_800_000_000)
        self.assertEqual(self.rows(), [])

    def test_rows_older_than_fourteen_days_are_pruned(self):
        self.store.receive(beat(), now=1_800_000_000)
        self.store.receive(beat(), now=1_800_000_000 + 14 * 86400 + 5)
        self.assertEqual(len(self.rows()), 1)

    def test_reopening_existing_database_keeps_log(self):
        self.store.receive(beat(), now=1_800_000_000)
        again = self.module.HeartbeatStore(self.path)
        again.receive(beat(), now=1_800_000_120)
        self.assertEqual(len(self.rows()), 2)

    def test_old_database_without_log_table_is_upgraded_in_place(self):
        base = Path(__file__).resolve().parents[2] / 'deploy/icbu/heartbeat-log/icbu_probe_heartbeat.base.py'
        spec = importlib.util.spec_from_file_location('icbu_probe_heartbeat_base', base)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        legacy = Path(self.tmp.name) / 'legacy.sqlite3'
        old.HeartbeatStore(legacy).receive(beat(), now=1_800_000_000)
        upgraded = self.module.HeartbeatStore(legacy)
        upgraded.receive(beat(), now=1_800_000_120)
        with sqlite3.connect(legacy) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM heartbeat_log').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM probe_state').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
