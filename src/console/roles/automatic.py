"""Rule-only triage with a durable ten-minute admission clock."""
import math


class AutomaticTriage:
    def __init__(self, roles, store, clock):
        self.roles, self.store, self.clock = roles, store, clock

    def poll(self):
        # Claim before scanning, including empty/failed scans. A restart cannot
        # immediately repeat work. Worker holds its process-shared file lease;
        # the store transaction also serializes independent admissions.
        now = self.clock()
        with self.store.write() as db:
            row = db.execute("SELECT position FROM operator_cursors WHERE name='triage'").fetchone()
            if row and now * 1000 - row['position'] < 600000:
                return dict(skipped=True)
            db.execute("INSERT INTO operator_cursors VALUES('triage','clock-ms',?) "
                       'ON CONFLICT(name) DO UPDATE SET generation=excluded.generation,position=excluded.position',
                       (math.ceil(now * 1000),))
        return self.roles.scan_triage('分拣员(自动)', apply=True, use_ai=False)
