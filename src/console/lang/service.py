"""Language settings and reviewable text only; no channel writes or send calls."""
import os
import re
import time

from console.archive.validation import BadRequest, Missing
from console.inbox import ai as inbox_ai
from console.inbox.model import decode_key
from console.roles.storage import run_lock
from . import prompts
from .detection import CODES, COUNTRIES, LANGUAGES, vote
from .protection import facts, validate
from .storage import DEFAULT_TERMS, LANG_PATH, RateLimited, Store
from .translation import chunks, digest, translate_blocks

FIELDS = {
    'settings': {'my_language', 'auto_native_send'},
    'glossary_save': {'id', 'term', 'mode', 'translation'},
    'glossary_delete': {'id'},
    'contact': {'conversation_key', 'native_language', 'locked'},
    'detect': {'conversation_key'},
    'translate': {'conversation_key', 'target_language', 'message_indices'},
    'compose': {'conversation_key', 'text'},
}


def language(value):
    if value not in CODES:
        raise BadRequest('请选择列表中的语言')
    return value


def identifier(value):
    if not re.fullmatch(r'[0-9]{1,12}', value or '') or int(value) < 1:
        raise BadRequest('术语编号无效，请刷新列表后重试')
    return int(value)


class Lang:
    rate_error = RateLimited

    def __init__(self, path=LANG_PATH, *, inbox, runner=None, clock=None, hourly_limit=None):
        self.store = Store(path)
        self.inbox = inbox
        self.runner = runner if runner is not None else inbox_ai.official_runner
        self.clock = clock if clock is not None else time.time
        raw_limit = hourly_limit if hourly_limit is not None else os.environ.get('IMGW_LANG_HOURLY_LIMIT', '120')
        if isinstance(raw_limit, bool) or not re.fullmatch(r'[0-9]{1,5}', str(raw_limit)) or not 1 <= int(raw_limit) <= 10000:
            raise ValueError('翻译小时额度须为 1 至 10000')
        self.hourly_limit = int(raw_limit)

    def invoke(self, prompt):
        self.store.reserve(self.clock(), self.hourly_limit)
        # The official runner owns its limited internal fallback attempts. Never
        # wrap it in an automatic retry loop or log its input/output/exceptions.
        return self.runner(prompt)

    def _settings(self):
        with self.store.read() as db:
            row = db.execute('SELECT my_language,auto_native_send FROM settings WHERE id=1').fetchone() if db else None
        return dict(my_language=row['my_language'], auto_native_send=bool(row['auto_native_send'])) if row else dict(
            my_language='zh-CN', auto_native_send=True)

    def _glossary(self):
        with self.store.read() as db:
            if db:
                return [dict(row) for row in db.execute('SELECT * FROM glossary ORDER BY id')]
        return [dict(id=i, term=term, mode='keep', translation='') for i, term in enumerate(DEFAULT_TERMS, 1)]

    def _conversation(self, key):
        try:
            channel, _, _ = decode_key(key, 'conversation')
        except ValueError:
            raise BadRequest('会话标识无效，请从收件箱重新打开会话') from None
        conv = self.inbox.detail(key)
        if conv is None:
            raise Missing('未找到会话，请从收件箱重新选择')
        if conv.get('channel') != channel or conv.get('key') != key:
            raise BadRequest('会话渠道与标识不一致，请重新打开会话')
        return conv

    def _contact(self, conv):
        key, channel = conv['key'], conv['channel']
        with self.store.read() as db:
            row = db.execute('SELECT * FROM contacts WHERE channel=? AND conversation_key=?', (channel, key)).fetchone() if db else None
        value = dict(row) if row else dict(channel=channel, conversation_key=key, native_language=None,
                                         source=None, confidence=0.0, updated_at=None)
        country = str(conv.get('country') or '').casefold().strip()
        value.update(locked=value['source'] == 'manual', display_name=conv.get('buyer_name') or '未命名联系人',
                     country_hint=COUNTRIES.get(country))
        return value

    def read(self, resource, values=None):
        values = values or {}
        if resource == 'settings':
            return dict(settings=self._settings(), languages=[dict(code=code, label=label) for code, label in LANGUAGES],
                        quota=self.store.quota(self.clock(), self.hourly_limit))
        if resource == 'glossary':
            return dict(items=self._glossary())
        if resource == 'contact':
            return dict(contact=self._contact(self._conversation(values.get('conversation_key', ''))))
        if resource == 'contacts':
            with self.store.read() as db:
                rows = list(db.execute('SELECT * FROM contacts ORDER BY updated_at DESC LIMIT 500')) if db else []
            items = []
            for row in rows:
                try:
                    items.append(self._contact(self._conversation(row['conversation_key'])))
                except Missing:
                    item = dict(row)
                    item.update(locked=item['source'] == 'manual', display_name='会话暂不可用', country_hint=None)
                    items.append(item)
            return dict(items=items)
        raise BadRequest('语言操作无效')

    def write(self, operation, fields, who):
        if operation not in FIELDS or not isinstance(fields, dict) or set(fields) - FIELDS[operation]:
            raise BadRequest('语言表单含有未支持的字段')
        if any(not isinstance(value, str) for value in fields.values()):
            raise BadRequest('语言表单字段须为文本')
        try:
            for value in fields.values():
                value.encode('utf8')
        except UnicodeError:
            raise BadRequest('语言表单文字编码无效，请重新输入') from None
        with run_lock(self.store):
            if operation == 'settings':
                old = self._settings()
                target = language(fields.get('my_language', old['my_language']))
                toggle = fields.get('auto_native_send', '1' if old['auto_native_send'] else '0')
                if toggle not in ('0', '1'):
                    raise BadRequest('请明确选择是否默认按客户母语起草')
                with self.store.write() as db:
                    db.execute('UPDATE settings SET my_language=?,auto_native_send=? WHERE id=1', (target, int(toggle)))
                    self.store.audit(db, self.clock(), who, operation, fields)
                return self.read('settings')
            if operation.startswith('glossary_'):
                return self._write_glossary(operation, fields, who)
            conv = self._conversation(fields.get('conversation_key', ''))
            if operation == 'contact':
                locked = fields.get('locked', '1')
                if locked not in ('0', '1'):
                    raise BadRequest('请选择锁定语言或恢复自动识别')
                native = language(fields.get('native_language', '')) if locked == '1' else None
                self._save_contact(conv, native, 'manual' if locked == '1' else None, 1.0 if locked == '1' else 0.0, who)
                return dict(contact=self._contact(conv))
            if operation == 'detect':
                return self._detect(conv, who)
            if operation == 'translate':
                result = self._translate(conv, fields)
                with self.store.write() as db:
                    self.store.audit(db, self.clock(), who, operation, fields)
                return result
            return self._compose(conv, fields, who)

    def _write_glossary(self, operation, fields, who):
        row_id = identifier(fields['id']) if fields.get('id') else None
        if operation == 'glossary_delete':
            if row_id is None:
                raise BadRequest('请指定要删除的术语')
            with self.store.write() as db:
                if not db.execute('DELETE FROM glossary WHERE id=?', (row_id,)).rowcount:
                    raise Missing('未找到这条术语，请刷新列表')
                self.store.audit(db, self.clock(), who, operation, ('id',))
            return self.read('glossary')
        term, mode, replacement = (fields.get(name, '').strip() for name in ('term', 'mode', 'translation'))
        if not term or len(term) > 100 or '\n' in term or '\r' in term or mode not in ('keep', 'translate'):
            raise BadRequest('术语须为 1 至 100 字的单行文本，并选择保持原样或指定译法')
        if mode == 'translate' and (not replacement or len(replacement) > 200):
            raise BadRequest('请填写 1 至 200 字的指定译法')
        if mode == 'translate' and facts(term) != facts(replacement):
            raise BadRequest('指定译法不能改变数字、型号、金额或链接，请保持这些片段原样')
        if re.search(r'__KEEP_\d+__|\[\[/?\d+\]\]', term + replacement):
            raise BadRequest('术语不能使用翻译块或保留片段的内部标记')
        replacement = replacement if mode == 'translate' else ''
        with self.store.write() as db:
            if db.execute('SELECT id FROM glossary WHERE lower(term)=lower(?) AND id!=?', (term, row_id or 0)).fetchone():
                raise BadRequest('这条术语已经存在，请直接修改原条目')
            if row_id:
                if not db.execute('UPDATE glossary SET term=?,mode=?,translation=? WHERE id=?', (term, mode, replacement, row_id)).rowcount:
                    raise Missing('未找到这条术语，请刷新列表')
            else:
                if db.execute('SELECT count(*) FROM glossary').fetchone()[0] >= 500:
                    raise BadRequest('术语表最多保存 500 条，请先删除不再使用的条目')
                db.execute('INSERT INTO glossary(term,mode,translation) VALUES(?,?,?)', (term, mode, replacement))
            self.store.audit(db, self.clock(), who, operation, fields)
        return self.read('glossary')

    def _save_contact(self, conv, native, source, confidence, who):
        with self.store.write() as db:
            db.execute('INSERT INTO contacts VALUES(?,?,?,?,?,?) ON CONFLICT(channel,conversation_key) DO UPDATE SET '
                       'native_language=excluded.native_language,source=excluded.source,confidence=excluded.confidence,updated_at=excluded.updated_at',
                       (conv['channel'], conv['key'], native, source, confidence, self.clock()))
            self.store.audit(db, self.clock(), who, 'contact', ('native_language', 'source', 'confidence'))

    def _detect(self, conv, who):
        old = self._contact(conv)
        if old['locked']:
            return dict(contact=old, hint='已按手动锁定的语言处理；需要自动识别时先解除锁定')
        texts = [m['text'][:1200] for m in conv['messages'][-40:] if m.get('role') == 'buyer' and m.get('text', '').strip()][-20:]
        found = vote(texts, old['native_language'])
        if found and found['confidence'] >= .8:
            self._save_contact(conv, found['language'], 'auto', found['confidence'], who)
        elif texts:
            try:
                output = inbox_ai._answer_text(self.invoke(prompts.detection(texts[-10:])))
                match = re.fullmatch(r'\s*语言[:：]\s*([a-zA-Z-]+)\s*\n把握[:：]\s*(0(?:\.\d{1,3})?|1(?:\.0{1,3})?)\s*', output)
                if match and match[1] in CODES and float(match[2]) >= .85:
                    # Existing language cannot be overturned by a lone ambiguous line.
                    if not old['native_language'] or old['native_language'] == match[1] or len(set(texts)) >= 2:
                        self._save_contact(conv, match[1], 'auto', float(match[2]), who)
            except RateLimited:
                raise
            except Exception:
                pass
        contact = self._contact(conv)
        return dict(contact=contact, hint='请核对自动识别的语言，必要时手动锁定' if contact['native_language'] else
                    '还不能确定客户母语，请手动确认；国家信息只作提示')

    @staticmethod
    def _translation_view(groups, target):
        items = []
        for index, source, entries in groups:
            complete = all(entry.get('text') is not None for entry in entries)
            items.append(dict(index=index, source_hash=digest(source[:4000]), source_text=source[:4000],
                source_characters=len(source), source_truncated=len(source) > 4000,
                text=''.join(entry['text'] for entry in entries) if complete else None,
                error=None if complete else '这条消息还有未完成的翻译块，请单独重试；成功的块已保留',
                cached=complete and all(entry.get('cached') for entry in entries), complete=complete,
                chunks=[dict(index=i, text=entry.get('text'), error=entry.get('error')) for i, entry in enumerate(entries)]))
        return dict(target_language=target, items=items)

    def _translate(self, conv, fields):
        target = language(fields.get('target_language') or self._settings()['my_language'])
        messages = conv['messages'][-300:]
        raw = fields.get('message_indices', '')
        if raw:
            if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,19}', raw):
                raise BadRequest('每次最多选择 20 条消息，请使用逗号分隔的消息序号')
            indices = [int(value) for value in raw.split(',')]
            if len(set(indices)) != len(indices) or any(i >= len(messages) for i in indices):
                raise BadRequest('消息序号无效或重复，请刷新会话后重试')
        else:
            indices = list(range(max(0, len(messages) - 20), len(messages)))
        groups, entries, glossary = [], [], self._glossary()
        for index in indices:
            source = messages[index]['text']
            if len(source) > 64000:
                raise BadRequest('这条消息超过 64000 字，请拆成较短文本后分段翻译')
            parts = [dict(source=part, text=None, error='这一块尚未完成，请单独重试') for part in chunks(source, glossary=glossary)]
            groups.append((index, source, parts))
            entries.extend(parts)
        try:
            translate_blocks(self, entries, target, glossary)
        except RateLimited as error:
            error.result = self._translation_view(groups, target)
            raise
        return self._translation_view(groups, target)

    def _compose(self, conv, fields, who):
        contact = self._contact(conv)
        native = contact['native_language']
        if not native or (contact['source'] != 'manual' and contact['confidence'] < .8):
            raise BadRequest('还不知道客户母语，不能起草母语版。请先识别并核对，或手动锁定客户语言')
        source = fields.get('text', '').strip()
        if not source or len(source) > 4000:
            raise BadRequest('请填写 1 至 4000 字的原稿，再生成客户母语版')
        glossary, mine = self._glossary(), self._settings()['my_language']
        first = [dict(source=part, text=None, error=None) for part in chunks(source, glossary=glossary)]
        translate_blocks(self, first, native, glossary, strict=False)
        if any(entry.get('text') is None for entry in first):
            raise BadRequest('母语稿的数字、型号、金额、链接或术语未通过校验，请重新生成；成功的块已缓存')
        translated = ''.join(entry['text'] for entry in first)
        back = [dict(source=part, text=None, error=None) for part in chunks(translated, glossary=glossary)]
        try:
            translate_blocks(self, back, mine, glossary, strict=False)
        except RateLimited as error:
            error.result = dict(text=translated, native_language=native, back_translation=None, my_language=mine,
                                warnings=[dict(kind='error', message='核对稿还未生成，请稍后重试；母语稿已缓存', fragments=[])])
            raise
        if any(entry.get('text') is None for entry in back):
            raise BadRequest('母语稿已缓存，但回译核对稿的格式或保留片段未通过校验，请重试后核对')
        check = validate(source, translated, glossary)
        back_check = validate(translated, ''.join(entry['text'] for entry in back), glossary)
        safe = check['ok'] and back_check['ok']
        fragments = check['missing'] + check['added'] + back_check['missing'] + back_check['added']
        warnings = [dict(kind='error' if not safe else 'info',
                         message='母语稿或核对稿的数字、型号、金额、链接或术语有增减，请核对红色提示后重新生成' if not safe else
                         '已核对保留片段；请继续核对语气、含义与回译稿，采用后仍需人工确认发送', fragments=fragments)]
        check['ok'] = safe
        with self.store.write() as db:
            self.store.audit(db, self.clock(), who, 'compose', ('native_language', 'my_language', 'validation'))
        return dict(native_language=native, my_language=mine, text=translated,
                    back_translation=''.join(entry['text'] for entry in back), warnings=warnings, validation=check,
                    back_validation=back_check, contact=contact)
