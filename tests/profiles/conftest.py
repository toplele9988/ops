"""All conversations are fictional; no network or real model runner is allowed."""
import json
import re
import socket
import subprocess
from types import SimpleNamespace

import pytest

NOW = 1900000000.


def message(i, text=None, role='buyer'):
    return dict(role=role, text=text or f'虚构消息 {i}，请报产品价格', sent_at=NOW - 1000 + i, sender_label='虚构成员')


def conversation(messages=None, key='fictional-conversation', title='虚构客户'):
    messages = messages if messages is not None else [message(1)]
    return dict(key=key, channel='alibaba', buyer_name=title, last_seen=messages[-1]['sent_at'] if messages else NOW,
                messages=messages, n_messages=len(messages), is_group=False)


class Inbox:
    def __init__(self, conv):
        self.conv = conv

    def read(self, now):
        return dict(ready=True, conversations={self.conv['key']: self.conv})

    def detail(self, key, now):
        return self.conv


class Runner:
    def __init__(self):
        self.prompts = []
        self.fail = None
        self.answer = None

    def __call__(self, argv, **kw):
        assert argv == ['ai-task', 'run', 'bailian_free', '--model', 'auto']
        prompt = kw['input']
        self.prompts.append(prompt)
        if self.fail:
            if isinstance(self.fail, Exception):
                raise self.fail
            return SimpleNamespace(stdout=self.fail, returncode=0)
        if '\n消息：\n' in prompt:
            match = re.search(r'^#(\d+) (.+?) (buyer|seller) (.+?)：(.*)$', prompt.split('\n消息：\n')[1], re.M)
            from datetime import datetime, timezone
            at = float(re.search(r'\(sent_at=([0-9.]+)\)', match[4])[1])
            text = match[5]
            claim = 'quoted' if match[3] == 'seller' else 'paid' if '已付款' in text else 'inquiry'
            evidence = [dict(claim=claim, quote=text[:80], message_index=int(match[1]), sent_at=at)]
        else:
            parts = json.loads(prompt.split('不得把不同群成员合并成同一人。\n')[1])
            evidence = []
            for part in parts:
                for e in part['evidence']:
                    if e not in evidence:
                        evidence.append(e)
            evidence = evidence[:12]
        answer = dict(kind='customer', org_name='虚构星桥公司', people=[dict(name='虚构小林', role='采购')],
                      country='IL', country_name='以色列', language='en', products=['ZX 虚构摇头灯 20台'],
                      summary='虚构买家询问产品，进展以证据为准', confidence=.85, clues=['虚构采购线索'], evidence=evidence)
        if self.answer:
            answer = self.answer(prompt, answer)
        return SimpleNamespace(stdout=json.dumps(dict(status='ok', provider='dashscope', route='bailian_free',
                               actual_model='deepseek-v4.1-flash', answer='```json\n' + json.dumps(answer) + '\n```',
                               usage=dict(total_tokens=100))), returncode=0)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('fictional tests must never access network or real model')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    from console import ai_gateway
    monkeypatch.setattr(ai_gateway, 'run', blocked)
