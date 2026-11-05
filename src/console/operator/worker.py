"""Host-owned 45-second worker; process-shared file lease, bounded work, no sends."""
import fcntl
import logging
import threading
from .store import dumps

LOG = logging.getLogger(__name__)


class Background:
    """Run a slow poll (network or model calls) off the 45-second loop.

    One thread at a time; a failure surfaces on the next loop so health records it.
    """
    def __init__(self, poll):
        self.call, self.thread, self.error, self.result = poll, None, None, None

    def _target(self):
        try:
            self.result = self.call()
        except Exception as error:
            self.error = error

    def poll(self):
        if self.thread is not None and self.thread.is_alive():
            return dict(running=True)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        last, self.result = self.result, None
        self.thread = threading.Thread(target=self._target, daemon=True, name='operator-background')
        self.thread.start()
        return dict(started=True, last=last)


class Worker:
    def __init__(self, operator, events, sources, *, gaps=None, archive_events=None, triage=None, incremental=None,
                 extras=(), interval=45, batch=100,
                 seed_at_tail=False):
        if not 30 <= interval <= 60:
            raise ValueError('operator_interval_invalid')
        self.operator, self.events, self.sources = operator, events, sources
        self.gaps, self.interval, self.batch = gaps, interval, batch
        self.archive_events = archive_events
        self.triage = triage
        self.incremental = incremental
        # (health component name, object with poll()) pairs, e.g. names refresh, pretranslation.
        self.extras = tuple(extras)
        # Production starts at the current newest row: history is covered by dossiers,
        # and replaying years of messages would spend one model call per old message.
        self.seed_at_tail = seed_at_tail
        self.stop_event = threading.Event()
        self.thread = None
        self.tick_thread = None

    def _run(self, component, call):
        try:
            result = call()
            self.operator.store.health(component, self.operator.clock())
            return result
        except Exception as error:
            try:
                self.operator.store.health(component, self.operator.clock(), error)
            except Exception:
                LOG.error('operator health write failed component=%s', component)
            LOG.error('operator job failed component=%s type=%s', component, type(error).__name__)
            return None

    def ingest(self, source):
        if not source.path.exists():
            return 0
        store = self.operator.store
        with store.read() as db:
            cursor = db.execute('SELECT * FROM operator_cursors WHERE name=?', (source.name,)).fetchone()
        info = source.path.stat()
        generation = dumps([info.st_dev, info.st_ino])
        after = cursor['position'] if cursor and cursor['generation'] == generation else 0
        snapshot = source.scan(after, self.batch)
        if snapshot is None:
            return 0
        if cursor is None and self.seed_at_tail:
            with store.write() as db:
                store.decision(db, self.operator.clock(), 'source_seeded', dict(source=source.name, position=snapshot['high']))
                db.execute('INSERT INTO operator_cursors VALUES(?,?,?) ON CONFLICT(name) DO NOTHING',
                           (source.name, snapshot['generation'], snapshot['high']))
            return 0
        count = 0
        for row in snapshot['rows']:
            m = row['message']
            if row['error']:
                store.health('source.row.'+source.name, self.operator.clock(), ValueError())
                with store.write() as db:
                    store.decision(db,self.operator.clock(),'source_skip',dict(source=source.name,position=row['position'],reason=row['error']))
            if m and m['role'] == 'buyer':
                identity = (m['channel'], m['key'], m['stable_id'])
                with store.read() as db:
                    old = db.execute('SELECT 1 FROM operator_ingested WHERE channel=? AND conversation_key=? AND stable_id=?', identity).fetchone()
                if not old:
                    # The engine claims the stable event before model invocation.
                    # Crash between emit/ack replays that same claim, never the model.
                    self.events.inbound(m['key'], m['stable_id'])
                    count += 1
            with store.write() as db:
                if m and m['role'] == 'buyer':
                    db.execute('INSERT OR IGNORE INTO operator_ingested VALUES(?,?,?,?)', (*identity, self.operator.clock()))
                db.execute('INSERT INTO operator_cursors VALUES(?,?,?) ON CONFLICT(name) DO UPDATE '
                           'SET generation=excluded.generation,position=excluded.position',
                           (source.name, snapshot['generation'], row['position']))
        return count

    def run_once(self, *, include_tick=True):
        if self.operator.bridge.name != 'fake':
            raise ValueError('integration_requires_fake_bridge')
        # Both console services/workers share this lease. OS releases it on crash.
        lock = self.operator.store.path.parent / 'worker.lock'
        import os
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return dict(leased=False)
            result = dict(leased=True)
            for source in self.sources:
                result[source.name] = self._run('source.'+source.name, lambda s=source: self.ingest(s))
            if self.gaps:
                result['gaps'] = self._run('dossiers', self.gaps.poll)
            if self.archive_events:
                result['archive'] = self._run('archive', self.archive_events.poll)
            if self.triage:
                result['triage'] = self._run('triage', self.triage.poll)
            if self.incremental:
                result['dossier_incremental'] = self._run('dossier_incremental', self.incremental.poll)
            for name, job in self.extras:
                result[name] = self._run(name, job.poll)
            if include_tick:
                result['tick'] = self._run('tick', self.operator.tick)
            return result

    def tick_once(self):
        if self.operator.bridge.name != 'fake':
            raise ValueError('integration_requires_fake_bridge')
        import os
        fd=os.open(self.operator.store.path.parent/'tick.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as lease:
            try:
                fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            return self._run('tick',self.operator.tick)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        if not self.operator.store.path.exists():
            raise RuntimeError('operator_initialize_required')
        self.stop_event.clear()
        def loop():
            while not self.stop_event.is_set():
                self._run('worker', lambda:self.run_once(include_tick=False))
                self.stop_event.wait(self.interval)
        def ticking():
            while not self.stop_event.is_set():
                self._run('tick.worker',self.tick_once)
                self.stop_event.wait(self.interval)
        self.thread = threading.Thread(target=loop, name='operator-rehearsal', daemon=True)
        self.tick_thread = threading.Thread(target=ticking, name='operator-tick', daemon=True)
        self.thread.start()
        self.tick_thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in (self.thread,self.tick_thread):
            if thread:
                thread.join(timeout=5)
            if thread and thread.is_alive():
                LOG.warning('operator worker still finishing admitted work')
