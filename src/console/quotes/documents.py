"""Customer allowlist documents and language-service handoff; never sends."""
from collections import Counter
import hashlib
import json
import re

from console.archive import validation as v
from console.archive.service import get
from .calc import compute
from .text import render, pi_preview


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Documents:
    def _state_key(self, round):
        return digest([str(self.store.path.resolve()), round['id'], round['deal_id'], round['created_at']])

    def _document(self, db, round, lang='zh'):
        from .service import children
        lines = children(db, 'quote_lines', round['id'])
        costs = children(db, 'quote_costs', round['id'])
        state = self.state.get(self._state_key(round))
        terms = state.get('terms', {})
        result = compute(round, lines, costs, self.day())
        result['blocking'].extend(self._term_issues(terms))
        # No timestamp-only edits can invalidate an otherwise identical version.
        commercial = {key: value for key, value in round.items() if key not in ('updated_at','notes','status','sent_at')}
        deal = get(db, 'deals', round['deal_id'])
        conversations = self._customer_conversations(db, deal)
        source_hash = digest([commercial, lines, costs, terms, deal.get('customer_party_id'), conversations])
        text = render(round, lines, lang, terms)
        return dict(text=text, ready=not result['blocking'], blocking=result['blocking'], warnings=result['warnings'],
                    source_hash=source_hash, lang=lang), state, lines, conversations

    @staticmethod
    def _term_issues(terms):
        if not terms.get('confirmed') or terms.get('payment') != '30_70' or not terms.get('delivery_scope'):
            return [dict(code='terms_unconfirmed', message='请核对并确认本轮 30% 定金、70% 发货前付清和交付范围，再生成可用报价')]
        return []

    @staticmethod
    def _customer_conversations(db, deal):
        if not deal.get('customer_party_id'):
            return []
        return [dict(row) for row in db.execute(
            'SELECT c.conversation_key,c.party_id FROM deal_conversations c '
            'JOIN identities i ON i.conversation_key=c.conversation_key AND i.party_id=c.party_id '
            "JOIN parties p ON p.id=c.party_id AND p.kind='customer' "
            'WHERE c.deal_id=? AND c.party_id=? ORDER BY c.id', (deal['id'], deal['customer_party_id']))]

    def _native(self, rid, values, who):
        with self.store.read() as db:
            round = get(db, 'quote_rounds', rid)
            if round['status'] != 'draft':
                raise v.Conflict('这一轮已发出，请新建下一轮')
            doc, state, lines, conversations = self._document(db, round)
        key = values['conversation_key']
        if key not in {row['conversation_key'] for row in conversations}:
            raise v.BadRequest('请选择已关联本案客户身份的会话，不能使用供应方或其他客户会话')
        lang = self._language_service()
        contact = lang.read('contact', {'conversation_key': key})['contact']
        if not contact.get('native_language') or (not contact.get('locked') and contact.get('confidence', 0) < .8):
            raise v.BadRequest('客户母语尚未确认，请先到语言设置识别并核对，或手动锁定客户母语')
        if lang.read('settings')['settings']['my_language'] != 'zh-CN':
            raise v.BadRequest('中文回译需要把语言设置中的阅读语言设为简体中文，再生成母语报价')
        if doc['blocking']:
            raise v.Conflict('本轮还有未确认内容，请先处理报价页的阻断问题，再生成母语报价')
        source, replacements = doc['text'], {}
        # Entire customer names/units/dates stay literal; P1 already protects
        # money, models and DMX. These model-shaped tokens use its public path.
        literals = [' '.join(line['name'].split()) for line in lines] + [' '.join((line.get('unit') or '').split()) for line in lines] + [round.get('valid_until')]
        if 'QuoteLiteral-' in source:
            raise v.BadRequest('报价包含保留标记，请修改产品名称后重新生成')
        by_literal = {}
        for literal in sorted({item for item in literals if item}, key=len, reverse=True):
            token = 'QuoteLiteral-' + str(len(replacements) + 1).zfill(4)
            by_literal[literal] = token
            replacements[token] = literal
        if by_literal:
            source = re.sub('|'.join(re.escape(item) for item in by_literal), lambda match: by_literal[match[0]], source)
        output = lang.write('compose', {'conversation_key': key, 'text': source}, who)
        if (not output.get('validation', {}).get('ok') or not output.get('back_validation', {}).get('ok')
                or output.get('my_language') != 'zh-CN' or output.get('native_language') != contact['native_language']):
            raise v.BadRequest('母语稿或中文回译没有通过校验，请核对语言后重新生成；报价仍为草稿')
        expected = Counter(re.findall(r'QuoteLiteral-\d+', source))
        restored = []
        for value in (output.get('text'), output.get('back_translation')):
            if not isinstance(value, str) or Counter(re.findall(r'QuoteLiteral-\d+', value)) != expected:
                raise v.BadRequest('母语稿的型号、单位或有效期被改写，请重新生成；报价仍为草稿')
            for token, literal in replacements.items():
                value = value.replace(token, literal)
            restored.append(value)
        current_contact = lang.read('contact', {'conversation_key': key})['contact']
        if current_contact != contact:
            raise v.Conflict('客户语言刚刚改变，请重新生成母语报价')
        artifact = dict(text=restored[0], back_translation=restored[1], source_hash=doc['source_hash'],
            lang='native', native_language=output['native_language'], contact=contact, conversation_key=key,
            generated_by=who, generated_at=self.clock())
        # Recheck after the model call; quantity/price/terms/identity may change.
        with self.store.write() as db:
            current = get(db, 'quote_rounds', rid)
            fresh, state, _, _ = self._document(db, current)
            if current['status'] != 'draft' or fresh['source_hash'] != doc['source_hash']:
                raise v.Conflict('报价刚刚改变，旧译文不能使用，请重新生成')
            state['native'] = artifact
            self.state.save(self._state_key(current), state, who, 'native', str(self.clock()))
        return dict(artifact, ready=True, blocking=[], warnings=output.get('warnings', []))

    def _document_write(self, db, round, op, values, who, stamp):
        doc, state, lines, _ = self._document(db, round, values.get('lang', 'zh'))
        if op == 'terms':
            terms = {key: value for key, value in values.items() if key != 'confirm'}
            if state.get('terms', {}).get('confirmed') and all(state['terms'].get(key) == terms.get(key) for key in ('payment','delivery_scope','seller_name','seller_address')):
                return dict(ok=True, id=round['id'], reused=True)
            state['terms'] = terms
            state['terms'].update(confirmed=True, confirmed_by=who, confirmed_at=stamp)
            state.pop('native', None)
        else:
            if values['lang'] == 'native':
                doc = self._native_view(doc, state)
            elif values['lang'] not in ('zh','en'):
                raise v.BadRequest('请先生成中文、英文或客户母语报价')
            if doc['blocking'] or doc['source_hash'] != values['source_hash'] or doc['text'] != values['text']:
                raise v.Conflict('文本与本轮已确认版本不一致，请修改报价条件后重新生成并审阅')
            review = dict(source_hash=doc['source_hash'], lang=values['lang'], text=doc['text'],
                          reviewed_by=who, reviewed_at=stamp)
            history = state.get('reviews', [state['review']] if state.get('review') else [])
            existing = next((row for row in history if row['source_hash'] == review['source_hash'] and row['lang'] == review['lang'] and row['text'] == review['text']), None)
            if existing:
                review = existing
            else:
                history.append(review)
            state['reviews'] = history
            state['review'] = review
        self.state.save(self._state_key(round), state, who, op, stamp)
        return dict(ok=True, id=round['id'])

    def _native_view(self, doc, state):
        artifact = state.get('native')
        if not artifact or artifact['source_hash'] != doc['source_hash']:
            raise v.Conflict('本轮尚无有效母语稿，请先确认客户母语，再重新生成母语报价')
        contact = self._language_service().read('contact', {'conversation_key': artifact['conversation_key']})['contact']
        if contact != artifact['contact']:
            raise v.Conflict('客户母语或锁定状态已改变，请重新生成母语报价')
        return dict(doc, **artifact)

    def _pi(self, db, round):
        doc, state, lines, _ = self._document(db, round)
        preview = pi_preview(round, lines, state.get('terms', {}))
        return dict(preview, source_hash=doc['source_hash'], ready=doc['ready'] and not preview['missing'],
                    blocking=doc['blocking'], warnings=doc['warnings'])
