"""P1/P2 integration seam; providers return counts, never messages or translations."""
from typing import Callable, Mapping

from .readers import MissingSchema

CoverageReader = Callable[[float], tuple[int, int] | None]
CoverageReaders = Mapping[tuple[str, str], CoverageReader]
METRICS = {'attachments': '带附件消息占比', 'translations': '已翻译会话占比'}


def coverage(channel, readers, now):
    result = []
    for key, label in METRICS.items():
        item = dict(key=key, label=label, status='not_connected', numerator=None,
                    denominator=None, ratio=None, reason='未接入，等待覆盖率统计接入后显示。')
        reader = readers.get((channel, key))
        if reader is not None:
            try:
                counts = reader(now)
                if counts is not None:
                    numerator, denominator = counts
                    if type(numerator) is not int or type(denominator) is not int or not 0 <= numerator <= denominator:
                        raise ValueError('覆盖率计数无效')
                    item.update(status='ready', numerator=numerator, denominator=denominator,
                                ratio=numerator / denominator if denominator else None,
                                reason='按已接入的统计范围计算。' if denominator else '还没有可统计的数据，收到数据后再看。')
            except (FileNotFoundError, MissingSchema):
                pass
            except Exception:
                # Optional schema changes/failures must not hide the pipeline cards.
                item.update(status='unavailable', reason='覆盖率暂时读不到，请管理员核对统计库和接口。')
        result.append(item)
    return result


def wechat_attachments(path, timeout):
    from .readers import read_db, require
    with read_db(path, timeout) as db:
        require(db, 'messages', ('app_id', 'message_id'))
        require(db, 'company_media', ('app_id', 'message_id'))
        denominator = db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        numerator = db.execute("SELECT COUNT(*) FROM messages m WHERE EXISTS (SELECT 1 FROM company_media a WHERE a.app_id=m.app_id AND a.message_id=m.message_id)").fetchone()[0]
    return numerator, denominator
