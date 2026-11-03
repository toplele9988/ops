"""Isolated private NAS media registry. No provider, OCR, model or network calls.

The source is opened read-only beneath a caller-supplied trusted root. Content
identity is SHA-256; file paths and mtimes are observations, never chat identity.
This module does not touch the production Catalog or any existing media ledger.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time

EXTENSIONS = {'.jpg', '.mp4'}
MAX_BYTES = 128 * 1024 * 1024
SHA = re.compile(r'^[a-f0-9]{64}$')
LOCAL_FILESYSTEMS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs', 'jfs'}
IO_STAGES = frozenset(('source_open', 'source_read', 'source_stat', 'output_create',
    'output_write', 'output_sync', 'output_verify', 'output_publish', 'output_open', 'output_cleanup'))
VALIDATION_CODES = frozenset(('unsafe_path', 'invalid_source_file', 'unsafe_blob', 'blob_changed',
    'asset_conflict', 'observation_conflict', 'source_short_read', 'source_grew', 'source_changed',
    'source_path_changed', 'blob_conflict', 'source_size_changed_after_preflight'))


class ScanInterrupted(RuntimeError):
    """The scan cannot establish complete or stable coverage."""


class MediaIOError(OSError):
    """Keep the operation and errno, never a source path or exception message."""

    def __init__(self, stage, number):
        super().__init__(number, '媒体文件读写失败，请按错误阶段检查存储')
        self.stage = stage if stage in IO_STAGES else 'unclassified'


def io_call(stage, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except OSError as error:
        raise MediaIOError(stage, error.errno) from None


def io_code(error):
    reasons = {
        errno.EACCES: 'permission_denied', errno.EPERM: 'permission_denied',
        errno.ENOENT: 'not_found', errno.ESTALE: 'stale_handle', errno.EIO: 'io_error',
        errno.ELOOP: 'symlink', errno.ENOTDIR: 'not_directory',
        errno.EFBIG: 'file_too_large', errno.ENAMETOOLONG: 'name_too_long',
        errno.EILSEQ: 'name_encoding', errno.ENOSPC: 'disk_full', errno.EDQUOT: 'quota',
        errno.EROFS: 'read_only', errno.EMFILE: 'fd_limit', errno.ENFILE: 'fd_limit',
    }
    stage = error.stage if isinstance(error, MediaIOError) else 'unclassified'
    return stage + '_' + reasons.get(error.errno, 'other_io')


def mount_for_path(path, *, mountinfo='/proc/self/mountinfo'):
    def unescape(value):
        return re.sub(r'\\(040|011|012|134)', lambda m: chr(int(m.group(1), 8)), value)
    try:
        rows = []
        with open(mountinfo, encoding='utf-8') as fh:
            for line in fh:
                left, right = line.rstrip('\n').split(' - ', 1)
                before, after = left.split(), right.split()
                point = unescape(before[4])
                if str(path) == point or str(path).startswith(point.rstrip('/') + '/'):
                    rows.append((len(point), before[0], point, after[0], unescape(after[1])))
        if not rows:
            raise ScanInterrupted('mount_not_found')
        return max(rows)[1:]
    except (OSError, ValueError, IndexError):
        raise ScanInterrupted('mount_check_failed') from None


def mountinfo_guard(mountpoint, filesystem, source, *, mountinfo='/proc/self/mountinfo'):
    """Return a guard with an explicit expected Linux mount identity.

    Only the most specific mount containing root may qualify. The returned
    identity includes Linux's mount ID so a detach/re-mount stops this run.
    """
    mountpoint = str(Path(mountpoint))
    if not Path(mountpoint).is_absolute() or not filesystem or not source:
        raise ValueError('invalid_mount_expectation')
    def guard(root):
        row = mount_for_path(root, mountinfo=mountinfo)
        if row[1:] != (mountpoint, filesystem, source):
            raise ScanInterrupted('mount_identity_mismatch')
        return row
    return guard


def local_output_guard(filesystem, *, mountinfo='/proc/self/mountinfo'):
    if filesystem not in LOCAL_FILESYSTEMS:
        raise ValueError('output_filesystem_not_local')
    def guard(path):
        row = mount_for_path(path, mountinfo=mountinfo)
        if row[2] != filesystem:
            raise ScanInterrupted('output_filesystem_not_local')
        return row
    return guard


def signature(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)


def checked_root(path):
    path = Path(path)
    if not path.is_absolute() or os.path.realpath(path) != str(path):
        raise ValueError('invalid_root')
    if not path.is_dir():
        raise ValueError('invalid_root')
    return path


def private_output(root, out):
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts or os.path.realpath(out) != str(out):
        raise ValueError('invalid_output')
    if out == root or root in out.parents or out in root.parents:
        raise ValueError('output_overlaps_source')
    out.mkdir(mode=0o700, parents=False, exist_ok=True)
    s = out.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise ValueError('unsafe_output')
    return out


def nofollow_source(root, rel):
    rel = Path(rel)
    if rel.is_absolute() or not rel.parts or '..' in rel.parts or any(x.startswith('.') for x in rel.parts):
        raise ValueError('unsafe_path')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in rel.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        result = os.open(rel.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        s = os.fstat(result)
        if not stat.S_ISREG(s.st_mode) or not 0 < s.st_size <= MAX_BYTES:
            os.close(result)
            raise ValueError('invalid_source_file')
        return result, fd
    except BaseException:
        os.close(fd)
        raise


def nofollow_directory(root, rel):
    rel = Path(rel)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('unsafe_path')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in rel.parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def enumerate_candidates(root, guard, stats):
    """Guard directories before opening them, and return a conservative size.

    File sizes are summed by path, including duplicates, so this is an upper
    bound on new blobs as long as all sources stay at their observed size.
    """
    pending = [Path('.')]
    candidates = []
    total = 0
    while pending:
        rel_dir = pending.pop()
        path = root / rel_dir
        guard(path)
        try:
            fd = nofollow_directory(root, rel_dir)
            try:
                opened = os.fstat(fd)
                named = os.stat(path, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                    raise ScanInterrupted('directory_changed')
                with os.scandir(fd) as entries:
                    names = sorted(entry.name for entry in entries if not entry.name.startswith('.'))
                for name in names:
                    rel = rel_dir / name
                    child = root / rel
                    guard(child)
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        if Path(name).suffix.lower() in EXTENSIONS:
                            stats['rejected'] += 1
                            stats['errors']['source_symlink'] = stats['errors'].get('source_symlink', 0) + 1
                        guard(child)
                        continue
                    if info.st_dev != opened.st_dev:
                        raise ScanInterrupted('source_device_changed')
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(rel)
                    elif Path(name).suffix.lower() in EXTENSIONS:
                        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                            stats['rejected'] += 1
                            stats['errors']['invalid_source_file'] = stats['errors'].get('invalid_source_file', 0) + 1
                        else:
                            candidates.append((rel, info.st_size))
                            total += info.st_size
                    guard(child)
            finally:
                os.close(fd)
            guard(path)
        except OSError:
            raise ScanInterrupted('directory_unavailable') from None
    return candidates, total


def digest_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
            raise ValueError('unsafe_blob')
        h = hashlib.sha256()
        while data := os.read(fd, 1024 * 1024):
            h.update(data)
        after = os.fstat(fd)
        named = path.lstat()
        if signature(s) != signature(after) or signature(s) != signature(named):
            raise ValueError('blob_changed')
        return h.hexdigest(), s.st_size, s
    finally:
        os.close(fd)


def known_hashes(path):
    if path is None:
        return set()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('invalid_hash_list')
    hashes = {line.strip().lower() for line in path.read_text().splitlines() if line.strip()}
    if not all(SHA.fullmatch(v) for v in hashes):
        raise ValueError('invalid_hash_list')
    return hashes


def connection(out):
    db = out / 'media-registry.sqlite3'
    if db.exists() or db.is_symlink():
        s = db.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
            raise ValueError('unsafe_registry')
    else:
        fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    con = sqlite3.connect(db, timeout=10)
    con.execute('PRAGMA synchronous=FULL')
    version = con.execute('PRAGMA user_version').fetchone()[0]
    has_tables = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
    if (has_tables and version != 2) or (not has_tables and version not in (0, 2)):
        con.close()
        raise ValueError('registry_schema_mismatch')
    con.executescript('''
      CREATE TABLE IF NOT EXISTS assets (
        sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL, state TEXT NOT NULL,
        source_verified INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
      );
      CREATE TABLE IF NOT EXISTS roots (
        root_id TEXT PRIMARY KEY, root_path TEXT NOT NULL, observed_at REAL NOT NULL
      );
      CREATE TABLE IF NOT EXISTS observations (
        root_id TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
        bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, observed_at REAL NOT NULL,
        source_verified INTEGER NOT NULL DEFAULT 0, known_hash_match INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(root_id, relative_path, sha256, mtime_ns, known_hash_match)
      );
    ''')
    con.execute('PRAGMA user_version=2')
    return con


def validate_prior(con, root_id, rel, sha, source_stat, is_known):
    """Reject inconsistent prior records rather than ignoring or overwriting them."""
    existing = con.execute('SELECT bytes,state,source_verified,created_at FROM assets WHERE sha256=?', (sha,)).fetchone()
    if existing and (tuple(existing[:3]) != (source_stat.st_size, 'private_archived', 0)
                     or type(existing[3]) not in (int, float) or not math.isfinite(existing[3]) or existing[3] <= 0):
        raise ValueError('asset_conflict')
    observations = con.execute('SELECT bytes,mtime_ns,source_verified,known_hash_match,observed_at FROM observations '
                               'WHERE root_id=? AND relative_path=? AND sha256=?', (root_id, str(rel), sha)).fetchall()
    for obs in observations:
        if (obs[0] != source_stat.st_size or type(obs[1]) is not int or obs[2] != 0 or obs[3] not in (0, 1)
                or type(obs[4]) not in (int, float) or not math.isfinite(obs[4]) or obs[4] <= 0):
            raise ValueError('observation_conflict')
    return existing


def process_one(root, out, con, rel, known, guard, check_output):
    guard(root / rel)
    fd, parent = io_call('source_open', nofollow_source, root, rel)
    temp = None
    try:
        before = io_call('source_stat', os.fstat, fd)
        check_output(before.st_size)
        # Temp belongs to this one invocation; a hash-addressed final blob is
        # published without overwrite. No source file is opened for writing.
        temp_fd, temp_name = io_call('output_create', tempfile.mkstemp, prefix='.incoming-', dir=out)
        temp = Path(temp_name)
        digest = hashlib.sha256()
        try:
            with os.fdopen(temp_fd, 'wb') as dst:
                remaining = before.st_size
                while remaining:
                    data = io_call('source_read', os.read, fd, min(remaining, 1024 * 1024))
                    if not data:
                        raise ValueError('source_short_read')
                    digest.update(data)
                    io_call('output_write', dst.write, data)
                    remaining -= len(data)
                if io_call('source_read', os.read, fd, 1):
                    raise ValueError('source_grew')
                io_call('output_write', dst.flush)
                io_call('output_sync', os.fsync, dst.fileno())
            after = io_call('source_stat', os.fstat, fd)
            named = io_call('source_stat', os.stat, Path(rel).name, dir_fd=parent, follow_symlinks=False)
            if signature(before) != signature(after) or signature(before) != signature(named):
                raise ValueError('source_changed')
            verify_fd, verify_parent = io_call('source_open', nofollow_source, root, rel)
            try:
                if signature(before) != signature(os.fstat(verify_fd)):
                    raise ValueError('source_path_changed')
            finally:
                os.close(verify_fd)
                os.close(verify_parent)
            guard(root / rel)
            check_output(0)
            sha = digest.hexdigest()
            is_known = sha in known
            root_id = hashlib.sha256(str(root).encode()).hexdigest()
            validate_prior(con, root_id, rel, sha, before, is_known)
            final = out / (sha + '.blob')
            rehomed = False
            try:
                os.link(temp, final, follow_symlinks=False)
            except FileExistsError:
                got, n, old_blob = io_call('output_verify', digest_file, final)
                if got != sha or n != before.st_size:
                    raise ValueError('blob_conflict')
                if old_blob.st_nlink != 1:
                    # A crash can leave final+an unidentified old temp alias.
                    # Preserve every unknown alias; atomically replace only the
                    # verified final with this run's freshly verified temp.
                    guard(root / rel)
                    check_output(0)
                    if signature(io_call('output_verify', final.lstat)) != signature(old_blob):
                        raise ValueError('blob_changed')
                    io_call('output_publish', os.replace, temp, final)
                    temp = None
                    rehomed = True
            except OSError as error:
                raise MediaIOError('output_publish', error.errno) from None
            else:
                io_call('output_cleanup', temp.unlink)
                temp = None
            got, n, final_info = io_call('output_verify', digest_file, final)
            if got != sha or n != before.st_size or final_info.st_nlink != 1:
                raise ValueError('blob_conflict')
            dfd = io_call('output_open', os.open, out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                io_call('output_sync', os.fsync, dfd)
            finally:
                os.close(dfd)
            guard(root / rel)
            check_output(0)
            stamp = time.time()
            with con:
                existing = validate_prior(con, root_id, rel, sha, before, is_known)
                con.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?)',
                            (sha, before.st_size, 'private_archived', 0, stamp))
                con.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?)',
                            (root_id, str(rel), sha, before.st_size, before.st_mtime_ns, stamp, 0, int(is_known)))
                guard(root / rel)
                check_output(0)
            return 'rehomed' if rehomed else ('existing' if existing else 'archived')
        finally:
            if temp is not None:
                with contextlib.suppress(FileNotFoundError):
                    io_call('output_cleanup', temp.unlink)
    finally:
        os.close(fd)
        os.close(parent)


def run(root, out, known=None, *, mount_guard, output_guard, minimum_free_bytes,
        space_fn=lambda p: shutil.disk_usage(p).free,
        output_device_fn=lambda p: os.stat(p).st_dev):
    root = checked_root(root)
    if not callable(mount_guard) or not callable(output_guard):
        raise ValueError('mount_guard_required')
    if type(minimum_free_bytes) is not int or not 0 < minimum_free_bytes <= 2**50:
        raise ValueError('minimum_free_invalid')
    try:
        mount_identity = mount_guard(root)
        original = os.stat(root, follow_symlinks=False)
    except (OSError, ScanInterrupted):
        raise ScanInterrupted('source_mount_unavailable') from None
    def guard(path):
        try:
            found_mount = mount_guard(path)
            if found_mount != mount_identity:
                raise ScanInterrupted('source_mount_changed')
            current = os.stat(root, follow_symlinks=False)
            child = os.stat(path, follow_symlinks=False)
            if (child.st_dev != original.st_dev or
                    (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)):
                raise ScanInterrupted('source_mount_changed')
        except OSError:
            raise ScanInterrupted('source_mount_unavailable') from None
    out_path = Path(out)
    output_identity = output_guard(out_path.parent)
    output_device = output_device_fn(out_path.parent)
    if output_identity == mount_identity or output_device == original.st_dev:
        raise ScanInterrupted('output_on_source_device')
    out = private_output(root, out)
    def check_output(extra):
        try:
            if output_guard(out) != output_identity or output_device_fn(out) != output_device:
                raise ScanInterrupted('output_mount_changed')
            if output_identity == mount_identity or output_device == original.st_dev:
                raise ScanInterrupted('output_on_source_device')
            if space_fn(out) < minimum_free_bytes + extra:
                raise ScanInterrupted('insufficient_space')
        except OSError:
            raise ScanInterrupted('output_unavailable') from None
    check_output(0)
    known = known_hashes(known)
    lock = os.open(out / 'worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    s = os.fstat(lock)
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
        os.close(lock)
        raise ValueError('unsafe_lock')
    stats = {'archived': 0, 'existing': 0, 'rehomed': 0, 'rejected': 0, 'status': 'complete', 'errors': {},
             'source_files': 0, 'estimated_upper_bytes': 0, 'minimum_free_bytes': minimum_free_bytes}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            candidates, estimated = enumerate_candidates(root, guard, stats)
            stats['source_files'] = len(candidates)
            stats['estimated_upper_bytes'] = estimated
            check_output(estimated)
        except ScanInterrupted as error:
            stats['status'] = 'partial'
            code = str(error) if re.fullmatch(r'[a-z_]{1,64}', str(error)) else 'scan_interrupted'
            stats['errors'][code] = stats['errors'].get(code, 0) + 1
            return stats
        con = connection(out)
        try:
            root_id = hashlib.sha256(str(root).encode()).hexdigest()
            with con:
                previous = con.execute('SELECT root_path FROM roots WHERE root_id=?', (root_id,)).fetchone()
                if previous and previous[0] != str(root):
                    raise ValueError('root_identity_conflict')
                con.execute('INSERT OR IGNORE INTO roots VALUES (?,?,?)', (root_id, str(root), time.time()))
            for rel, estimated_size in candidates:
                try:
                    current = io_call('source_stat', os.stat, root / rel, follow_symlinks=False)
                    if current.st_size != estimated_size:
                        raise ValueError('source_size_changed_after_preflight')
                    stats[process_one(root, out, con, rel, known, guard, check_output)] += 1
                except (OSError, ValueError) as error:
                    stats['rejected'] += 1
                    if isinstance(error, OSError):
                        code = io_code(error)
                    else:
                        code = str(error) if str(error) in VALIDATION_CODES else 'source_validation_failed'
                    stats['errors'][code] = stats['errors'].get(code, 0) + 1
            guard(root)
            check_output(0)
            if stats['rejected']:
                stats['status'] = 'partial'
        except ScanInterrupted as error:
            stats['status'] = 'partial'
            code = str(error) if re.fullmatch(r'[a-z_]{1,64}', str(error)) else 'scan_interrupted'
            stats['errors'][code] = stats['errors'].get(code, 0) + 1
        finally:
            con.close()
    finally:
        os.close(lock)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--known-hashes', default=None)
    parser.add_argument('--mountpoint', required=True)
    parser.add_argument('--mount-source', required=True)
    parser.add_argument('--mount-fstype', required=True)
    parser.add_argument('--output-fstype', required=True)
    parser.add_argument('--minimum-free-bytes', required=True, type=int)
    args = parser.parse_args()
    os.umask(0o077)
    guard = mountinfo_guard(args.mountpoint, args.mount_fstype, args.mount_source)
    out_guard = local_output_guard(args.output_fstype)
    try:
        result = run(args.root, args.out, args.known_hashes, mount_guard=guard,
                     output_guard=out_guard, minimum_free_bytes=args.minimum_free_bytes)
    except ScanInterrupted as error:
        code = str(error) if re.fullmatch(r'[a-z_]{1,64}', str(error)) else 'scan_interrupted'
        print(json.dumps({'status': 'unavailable', 'errors': {code: 1}}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
