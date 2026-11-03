"""Private company brief with bounded, read-on-demand version history."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import threading

from console.inbox.store import read_database
from .text import clean

BRIEF_PATH = Path('/var/lib/imgw/ops-app/company-brief.md')
HISTORY_PATH = Path('/var/lib/imgw/ops-app/kb.sqlite3')
DEFAULT_BRIEF = '''公司从事舞台灯光出口业务：从广州的灯光工厂采购（频闪灯、面光灯、光束灯、特效设备等），卖给海外买家。
沟通渠道：阿里国际站（共 6 个账号）、微信、WhatsApp；以后还有独立站。微信里供应商群标 SUP，客户群标 CUS。

做生意的几条硬规则：
1. 报价要同时看产品价和到门运费。DDP（含税到门）价格可能出现倒挂：产品价加运费高于买家能接受的价，所以不要随口报价。
2. 也做 FOB 报价；FOB 和 DDP 要说清楚，不要混。
3. 运费取决于每个产品的外箱尺寸、毛重和每箱数量。供应商给的尺寸重量不准就会亏运费，没核对的数据要写【待确认】。
4. 物流报价都有有效期，价格一直在变。过期的、没写有效期的运价不能拿来报价。
5. 供应商报价是成本价，永远不能告诉买家，也不能让买家从回复里推算出来。

AI 回复准则：
- 不编造价格、库存、交期、运费或规格；拿不准就写【待确认：…】，不擅自承诺。
- 不泄露成本价、供应商信息或其他客户的信息。
- 语言跟随买家，语气礼貌简洁。如果买家抱怨在和机器人说话，要明确告诉他会有真人跟进，并马上给出实质答复。'''
_LOCK = threading.Lock()


class Brief:
    def __init__(self, path=BRIEF_PATH, history_path=HISTORY_PATH):
        self.path, self.history_path = Path(path), Path(history_path)

    def read(self):
        try:
            with self.path.open(encoding='utf8') as stream:
                return clean(stream.read(8001))[:8000]
        except FileNotFoundError:
            return DEFAULT_BRIEF

    def versions(self, limit=5):
        if not self.history_path.exists():
            return []
        with read_database(self.history_path) as db:
            return [dict(id=i, saved_at=t, saved_by=w) for i, t, w in db.execute(
                'SELECT id,saved_at,saved_by FROM brief_versions ORDER BY id DESC LIMIT ?', (limit,))]

    def version(self, version):
        if not self.history_path.exists():
            return None
        with read_database(self.history_path) as db:
            row = db.execute('SELECT id,saved_at,saved_by,text FROM brief_versions WHERE id=?', (version,)).fetchone()
        return dict(zip(('id', 'saved_at', 'saved_by', 'text'), row)) if row else None

    def save_brief(self, text, who):
        if not isinstance(text, str) or len(text) > 8000:
            raise ValueError('公司背景不能超过 8000 字')
        text = clean(text)
        if not text.strip():
            raise ValueError('公司背景不能为空')
        who = clean(who)[:100]
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.history_path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            self.history_path.chmod(0o600)
            fd, temporary = tempfile.mkstemp(prefix='.company-brief-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf8') as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                with closing(sqlite3.connect(self.history_path)) as db, db:
                    db.execute('CREATE TABLE IF NOT EXISTS brief_versions '
                        '(id INTEGER PRIMARY KEY AUTOINCREMENT,saved_at TEXT,saved_by TEXT,text TEXT)')
                    cursor = db.execute('INSERT INTO brief_versions(saved_at,saved_by,text) VALUES(?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), who, text))
                    version = cursor.lastrowid
                    db.execute('DELETE FROM brief_versions WHERE id NOT IN '
                               '(SELECT id FROM brief_versions ORDER BY id DESC LIMIT 20)')
                    os.replace(temporary, self.path)  # mkstemp is 0600, including replacement of old files.
                return version
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
