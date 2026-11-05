"""Host-owned polling hook. No thread, daemon, channel write or model retry."""
from console.roles.storage import run_lock


class PretranslateSchedule:
    def __init__(self, lang):
        self.lang = lang

    def poll(self):
        with run_lock(self.lang.store):
            now = self.lang.clock()
            with self.lang.store.write() as db:
                row = db.execute('SELECT next_at FROM pretranslate_schedule WHERE id=1').fetchone()
                if row and now < row['next_at']:
                    return dict(skipped=True, retry_after=max(1, int(row['next_at'] - now)))
                # Reserve the interval before work; multiple processes share this throttle.
                db.execute('INSERT INTO pretranslate_schedule VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next_at=excluded.next_at',
                           (now + 300,))
            return dict(self.lang.pretranslate(20), skipped=False)
