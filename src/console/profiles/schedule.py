"""Independent hourly process, using the existing operator admission cursor."""
import os
import subprocess
import sys


class ProfilesSchedule:
    def __init__(self, store, clock, *, popen=None, python=None, source_root=None, daily_budget_tokens=1000000):
        self.store, self.clock = store, clock
        self.popen, self.python = popen or subprocess.Popen, python or sys.executable
        self.source_root, self.daily_budget_tokens = source_root, daily_budget_tokens
        self.process = None

    def poll(self):
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                return dict(running=True)
            self.process = None
            if code:
                raise RuntimeError('profiles_exit_%d' % code)
            return dict(finished=True)
        now = self.clock()
        with self.store.write() as db:
            row = db.execute("SELECT position FROM operator_cursors WHERE name='profiles'").fetchone()
            if row and now * 1000 - row['position'] < 3600000:
                return dict(waiting=True)
            db.execute("INSERT INTO operator_cursors VALUES('profiles','clock-ms',?) "
                       'ON CONFLICT(name) DO UPDATE SET generation=excluded.generation,position=excluded.position',
                       (int(now * 1000),))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if self.source_root:
            env['PYTHONPATH'] = str(self.source_root)
        self.process = self.popen(['nice', '-n', '10', self.python, '-m', 'console.profiles', 'run', '--once',
                                  '--max-conversations', '20', '--daily-budget-tokens', str(self.daily_budget_tokens)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env, start_new_session=True)
        return dict(started=now)
