"""Read credentials only for an explicit operation; status examines metadata."""
import grp
import json
import os
from pathlib import Path
import stat

from .errors import OpenError


class Credentials:
    def __init__(self, path=None, *, expected_uid=0, expected_gid=None):
        self.path = Path(os.path.abspath(path or os.environ.get('OPS_ALIBABA_OPEN_CREDENTIALS',
                                                               '/etc/imgw/alibaba-open.json')))
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _check(self, info):
        gid = self.expected_gid
        if gid is None:
            gid = grp.getgrnam('imgw').gr_gid
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o640
                or info.st_uid != self.expected_uid or info.st_gid != gid
                or info.st_nlink != 1 or info.st_size > 8192):
            raise ValueError

    def _parents(self):
        for parent in self.path.parents:
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError

    def _open(self):
        # Walk directory descriptors so replacing a parent with a symlink cannot
        # redirect a credential read between the metadata check and open.
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.path.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            return os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)

    def status(self):
        try:
            self._parents()
            self._check(self.path.lstat())
        except FileNotFoundError:
            return dict(configured=False, message='未配置应用凭据，请管理员运行凭据保存脚本')
        except (OSError, KeyError, ValueError):
            return dict(configured=False, message='应用凭据权限不正确，请管理员核对所有者和 0640 权限')
        return dict(configured=True, message='应用凭据文件已配置，使用时会核对内容')

    def load(self):
        try:
            self._parents()
            before = self.path.lstat()
            self._check(before)
            fd = self._open()
            with os.fdopen(fd, 'r', encoding='utf8') as stream:
                actual = os.fstat(stream.fileno())
                self._check(actual)
                if (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
                    raise ValueError
                data = json.loads(stream.read(8193))
                after = os.fstat(stream.fileno())
                self._check(after)
                if (actual.st_size, actual.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError
            if (not isinstance(data, dict) or data.get('schema') != 'alibaba-open-v1'
                    or any(not isinstance(data.get(k), str) or not data[k].strip()
                           or len(data[k]) > 1024 for k in ('app_key', 'app_secret'))):
                raise ValueError
            return {k: data[k] for k in ('app_key', 'app_secret')}
        except (OSError, KeyError, ValueError, TypeError, UnicodeError):
            raise OpenError('应用凭据未配置或格式、权限不正确，请管理员重新保存凭据') from None
