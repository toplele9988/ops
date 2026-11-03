"""重复运行的离线演练：临时库、假 runner、禁止网络，不操作服务。"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
import tempfile

from pytest import MonkeyPatch

from recovery_fixtures import live_fixture


def run():
    with tempfile.TemporaryDirectory(prefix='kb-recovery-rehearsal-') as directory, MonkeyPatch.context() as patch:
        def blocked(*args, **kwargs):
            raise AssertionError('演练禁止访问真实网络')
        patch.setattr(socket.socket, 'connect', blocked)
        patch.setattr(socket.socket, 'connect_ex', blocked)
        patch.setattr(socket, 'create_connection', blocked)
        f = live_fixture(Path(directory), patch)
        before = deepcopy(f.frozen.ledger)
        f.add(1, 800)
        f.add(2, 900, admitted=1100)
        assert len(f.matrix.preflight()) == 3
        assert f.tick() == []
        assert f.db.execute('SELECT count(*) FROM kb_live_state').fetchone()[0] == 0
        f.add(3, 1100)
        assert f.tick()[0]['units'] == 1
        for _ in range(6):
            assert f.tick() == []
        assert len(f.requests) == 2
        with sqlite3.connect(f.frozen.path) as calls:
            held = calls.execute("SELECT * FROM calls WHERE state='hold' ORDER BY cache_key").fetchall()
            complete = calls.execute("SELECT count(*) FROM calls WHERE state='complete'").fetchone()[0]
        assert held == f.held_rows and f.frozen.ledger == before
        assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 1100
        f.db.close()
        return {'结果': '通过', '演练数据': '全部虚构', '历史请求次数': 0,
                '自然增量单元': 1, '假 runner 请求次数': 2, '成功响应检查点': complete,
                '重复轮询新增请求': 0, '原 hold 保持不变': 3, '生产写入': 0}


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
