"""冻结未知请求的来源；只校验摘要，不读取来源、凭据或调用模型。"""
from __future__ import annotations

import hashlib
import re


def _need(value):
    if not value:
        raise ValueError('未知来源隔离清单不完整，请重新核对四条未知请求')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def verify(value):
    _need(type(value) is dict and set(value) == {'schema', 'holds'}
          and value['schema'] == 'kb-source-quarantine-v1'
          and type(value['holds']) is list and len(value['holds']) == 4)
    channels = []
    requests = set()
    for row in value['holds']:
        _need(type(row) is dict and set(row) == {
            'channel', 'request_sha256', 'source_keys', 'source_sha256',
            'content_sha256', 'prompt_sha256'})
        channels.append(row['channel'])
        for key in ('request_sha256', 'prompt_sha256'):
            _need(type(row[key]) is str and re.fullmatch('[0-9a-f]{64}', row[key]))
        _need(row['request_sha256'] not in requests)
        requests.add(row['request_sha256'])
        for key in ('source_keys', 'source_sha256', 'content_sha256'):
            _need(type(row[key]) is list and bool(row[key])
                  and all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) for x in row[key])
                  and len(set(row[key])) == len(row[key]))
    _need(channels.count('wechat') == 3 and channels.count('alibaba') == 1)
    return value


def check(rows, quarantine, prompt_sha256=None):
    value = verify(quarantine)
    keys = {key for hold in value['holds'] for key in hold['source_keys']}
    originals = {key for hold in value['holds'] for key in hold['source_sha256']}
    bodies = {key for hold in value['holds'] for key in hold['content_sha256']}
    prompts = {hold['prompt_sha256'] for hold in value['holds']}
    _need(prompt_sha256 is None or prompt_sha256 not in prompts)
    for row in rows:
        _need(isinstance(row['evidence_id'], str) and isinstance(row['body_text'], str))
        if (digest(row['evidence_id']) in keys or digest(row['body_text']) in bodies
                or row['source_sha256'] in originals):
            raise ValueError('资料命中永久隔离的未知请求，请保留原记录并交总指挥核对')


def bind_wechat(quarantine, isolation):
    verify(quarantine)
    actual = {(row['request_sha256'], row['prompt_sha256'])
              for row in quarantine['holds'] if row['channel'] == 'wechat'}
    expected = {(row['checkpoint_sha256'], row['prompt_sha256']) for row in isolation['holds']}
    _need(len(expected) == 3 and actual == expected)
