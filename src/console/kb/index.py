"""Lazy read-only catalog snapshots. No full float32 matrix or full-text cache."""
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from console.inbox.store import read_database
from .policy import FAMILIES, access_flags, classify, type_label
from .text import title, tokens

CATALOG_PATH = Path('/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3')
MODEL_VERSION = 'BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181'
DIMENSIONS = 1024


def numpy_module():
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def iso_date(created_at, mtime_ns=None):
    try:
        result = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.isoformat()
    except (ValueError, TypeError):
        try:
            return datetime.fromtimestamp(mtime_ns / 1e9, timezone.utc).isoformat() if mtime_ns else None
        except (ValueError, TypeError, OverflowError, OSError):
            return None


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    asset_id: str
    scope: str
    parent: int
    child: int
    text: str
    family: str
    title: str
    type_label: str
    date: str | None
    confidential: bool
    is_cost_price: bool


@dataclass(frozen=True)
class Snapshot:
    chunks: tuple
    vectors: object
    postings: dict
    lengths: array
    average_length: float
    with_vectors: int
    loaded_at: str
    load_seconds: float
    assets_by_type: tuple
    scopes: frozenset


class Index:
    def __init__(self, path=CATALOG_PATH, *, clock=time.monotonic, reload_seconds=120, use_numpy=True):
        self.path = Path(path)
        self.clock, self.reload_seconds, self.use_numpy = clock, reload_seconds, use_numpy
        self._snapshot = None
        self._stamp = None
        self._last_load = float('-inf')
        self._lock = threading.Lock()

    def _signature(self):
        def stat(path):
            try:
                s = path.stat()
                return s.st_mtime_ns, s.st_size, s.st_ino
            except FileNotFoundError:
                return None
        main = stat(self.path)
        return (main, stat(Path(str(self.path) + '-wal'))) if main else None

    def snapshot(self):
        stamp, now = self._signature(), self.clock()
        current = self._snapshot
        if stamp is None:
            return None
        if current is not None and (stamp == self._stamp or now - self._last_load < self.reload_seconds):
            return current
        # A reload never holds up readers that already have a complete snapshot.
        if not self._lock.acquire(blocking=current is None):
            return current
        try:
            stamp, now = self._signature(), self.clock()
            if stamp is None:
                return None
            if self._snapshot is not None and (stamp == self._stamp or now - self._last_load < self.reload_seconds):
                return self._snapshot
            replacement = self._load()
            self._snapshot, self._stamp, self._last_load = replacement, stamp, self.clock()
            return replacement
        finally:
            self._lock.release()

    def _load(self):
        started = time.perf_counter()
        np = numpy_module() if self.use_numpy else None
        chunks, postings, lengths = [], {}, array('I')
        asset_types, seen_assets, scopes = Counter(), set(), set()
        with read_database(self.path) as db:
            db.execute('BEGIN')  # Count, metadata, ACL and chunks share a SQLite read snapshot.
            count = db.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0]
            vectors = np.zeros((count, DIMENSIONS), dtype=np.float16) if np is not None else None
            labels = {}
            # Labels record the originating scope, but file confidentiality follows the hash
            # across copies in other scopes (same rule as production business_access.py).
            for sha, owner, scope, confidentiality, cost in db.execute(
                    'SELECT source_sha256, owner, source_scope, confidentiality, is_cost_price FROM catalog_access_labels'):
                if sha:
                    labels.setdefault(sha, []).append((confidentiality, cost))
            registry = {}
            for owner, scope, name, role in db.execute(
                    'SELECT owner, source_scope, display_name, channel_role FROM channel_role_registry ORDER BY observed_at'):
                registry[owner, scope] = (name, role)
            assets = {}
            for aid, owner, scope, filename, kind, created, mtime, sha in db.execute(
                    'SELECT asset_id, owner, source_scope, filename, source_type, created_at, source_file_mtime_ns, '
                    'source_sha256 FROM multimedia_assets'):
                name, role = registry.get((owner, scope), ('', ''))
                family = classify(kind, scope, role)
                confidential, cost = access_flags(labels.get(sha, ()), kind, family)
                assets[aid, owner, scope] = (family, title(name or filename, FAMILIES[family]),
                    type_label(kind), iso_date(created, mtime), confidential, cost)
            with_vectors = 0
            for cid, aid, owner, scope, parent, child, body, blob, version in db.execute(
                    'SELECT chunk_id, asset_id, owner, source_scope, parent_chunk, child_chunk, substr(text,1,1200), '
                    'dense_vector, model_version FROM knowledge_chunks ORDER BY asset_id,parent_chunk,child_chunk,chunk_id'):
                meta = assets.get((aid, owner, scope))
                if meta is None:
                    # Broken provenance is never admitted to customer material.
                    meta = ('other', FAMILIES['other'], '其他资料', None, True, False)
                i = len(chunks)
                chunks.append(Chunk(cid, aid, scope, parent, child, body, *meta))
                scopes.add(scope)
                if aid not in seen_assets:
                    seen_assets.add(aid)
                    asset_types[meta[2]] += 1
                counts = Counter(tokens(body))
                lengths.append(sum(counts.values()))
                for word, frequency in counts.items():
                    ids, frequencies = postings.setdefault(word, (array('I'), array('H')))
                    ids.append(i)
                    frequencies.append(frequency)
                if version == MODEL_VERSION and isinstance(blob, bytes) and len(blob) == DIMENSIONS * 4:
                    if np is None:
                        with_vectors += 1
                    else:
                        vector = np.frombuffer(blob, dtype='<f4')
                        norm = np.linalg.norm(vector)
                        if np.isfinite(vector).all() and np.isfinite(norm) and norm > 0:
                            vectors[i] = vector / norm
                            with_vectors += 1
            if vectors is not None:
                vectors.flags.writeable = False
        return Snapshot(tuple(chunks), vectors, postings, lengths, sum(lengths) / max(1, len(lengths)),
            with_vectors, datetime.now(timezone.utc).isoformat(), time.perf_counter() - started,
            tuple(asset_types.most_common(10)), frozenset(scopes))

    def full_text(self, chunk_id):
        """Internal, on-demand full text; callers must apply purpose policy before using it."""
        with read_database(self.path) as db:
            row = db.execute('SELECT text FROM knowledge_chunks WHERE chunk_id=?', (chunk_id,)).fetchone()
            return row[0] if row else None

    def conversation_scope(self, channel, account, conversation_id):
        # Verified from icbu_history.scope_for/canonical. Never guess a binding ID.
        if channel != 'alibaba' or not re.fullmatch(r'tm:[A-Za-z0-9_-]{1,128}', conversation_id):
            return None
        digest = hashlib.sha256(json.dumps([account, conversation_id], ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        snapshot = self.snapshot()
        matches = [s for s in snapshot.scopes if re.fullmatch(r'icbu-tm:[A-Za-z0-9_-]{1,48}:' + digest, s)] if snapshot else []
        return matches[0] if len(matches) == 1 else None
