"""Daily dossier update, started from the console's operator worker.

The update runs in its own process (OCR is CPU heavy), at most once per Beijing
calendar day after the configured hour. The day is claimed before starting, so
a crash or restart never runs it twice; the next day runs normally.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

BEIJING = timezone(timedelta(hours=8))
CURSOR = 'dossier-incremental'


class DailyIncremental:
    def __init__(self, store, clock, *, hour=2, log_dir=None, popen=None, python=None, source_root=None):
        self.store, self.clock, self.hour = store, clock, hour
        self.log_dir = Path(log_dir) if log_dir else None
        self.popen = popen or subprocess.Popen
        self.python = python or sys.executable
        self.source_root = source_root
        self.process = None

    def poll(self):
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                return dict(running=True)
            self.process = None
            if code != 0:
                # Health records the failure; the summary file holds the reason code.
                raise RuntimeError('dossier_incremental_exit_%d' % code)
            return dict(finished=True)
        now = datetime.fromtimestamp(self.clock(), BEIJING)
        if now.hour < self.hour:
            return dict(waiting=True)
        day = now.date().isoformat()
        with self.store.write() as db:
            row = db.execute('SELECT generation FROM operator_cursors WHERE name=?', (CURSOR,)).fetchone()
            if row and row['generation'] >= day:
                return dict(done_today=True)
            db.execute('INSERT INTO operator_cursors VALUES(?,?,0) ON CONFLICT(name) DO UPDATE SET generation=excluded.generation',
                       (CURSOR, day))
        out = subprocess.DEVNULL
        if self.log_dir:
            self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            out = open(self.log_dir / (day + '.json'), 'w')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if self.source_root:
            env['PYTHONPATH'] = str(self.source_root)
        try:
            self.process = self.popen(['nice', '-n', '10', self.python, '-m', 'console.dossiers.incremental', 'run', '--once'],
                                      stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env,
                                      start_new_session=True)
        finally:
            if out is not subprocess.DEVNULL:
                out.close()
        return dict(started=day)


def last_summary(log_dir):
    """Newest run summary for the dossier page; None when nothing ran yet."""
    try:
        files = sorted(Path(log_dir).glob('*.json'))
        return dict(day=files[-1].stem, **json.loads(files[-1].read_text() or '{}')) if files else None
    except (OSError, ValueError):
        return None
