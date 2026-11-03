"""可重复的双队列隔离演练；只有虚构来源、假 runner 和临时进度库。"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile

from pytest import MonkeyPatch

from test_backlog import Harness, b, entry, quarantine


def run():
    with tempfile.TemporaryDirectory(prefix='kb-backlog-rehearsal-', dir='/tmp') as directory, \
            MonkeyPatch.context() as patch:
        def blocked(*args, **kwargs):
            raise AssertionError('演练禁止访问真实网络')
        patch.setattr(socket.socket, 'connect', blocked)
        patch.setattr(socket.socket, 'connect_ex', blocked)
        patch.setattr(socket, 'create_connection', blocked)
        h = Harness(Path(directory), [entry(1), entry(2), entry(3, reason='local_pending'),
                                     entry(4, reason='complete')])
        live = entry(5, at=1100)
        h.sources[live['source_id']] = deepcopy(live)
        h.scheduler.admit_live([live], current_quarantine=quarantine())
        assert h.run(backlog_limit=1) == 2
        assert h.requests == [live['source_id']]
        h.scheduler = h.restart()
        assert h.run(backlog_limit=1) == 1
        h.scheduler = h.restart()
        assert h.run(backlog_limit=1) == 1
        assert h.run() == 0
        assert len(h.requests) == len(set(h.requests)) == 3
        assert set(h.local) == {entry(n)['source_id'] for n in (1, 2, 3, 5)}
        tampered = deepcopy(h.manifest)
        tampered['items'][0]['fingerprint'] = '0' * 64
        refused = 0
        try:
            b.validate_manifest(tampered, b.digest(h.manifest), quarantine())
        except b.ReviewError:
            refused += 1
        unknown = entry(6)
        unknown['source_id'] = '虚构未知来源0'
        try:
            h.scheduler.admit_live([dict(unknown, source_at=1200, admitted_at=1200)],
                                   current_quarantine=quarantine())
        except b.ReviewError:
            refused += 1
        assert refused == 2
        return {'结果': '通过', '演练数据': '全部虚构', '新增完成': 1, '积压完成': 3,
                '原已完成跳过': 1, '仅本地续做': 1, '假runner请求': len(h.requests),
                '重启后重复请求': 0, '未知重发': 0, '篡改与未知交叉拒绝': refused, '生产写入': 0}


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
