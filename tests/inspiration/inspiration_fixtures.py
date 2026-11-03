"""All data in this module is invented; no live message/card is used."""
import json
import sqlite3

from console.inspiration.cards import parse_finder_card

AUTHOR = '示例号甲'
DESCRIPTION = ('介绍一个虚构的开源工具 DemoKit，用法：把 skill 放进 skills 目录 '
               'https://demo.invalid/kit #AI #开源')


def payload(description=DESCRIPTION, author=AUTHOR, *, xml=False):
    if xml:
        attachment = dict(appmsg_type=51, xml=(
            '<msg><appmsg><finderFeed><nickname><![CDATA[' + author + ']]></nickname>'
            '<desc><![CDATA[' + description + ']]></desc><mediaCount>1</mediaCount>'
            '</finderFeed></appmsg></msg>'))
    else:
        attachment = dict(finder_author=author, finder_description=description)
    return json.dumps(dict(msg_type=49, attachments=[attachment]), ensure_ascii=False)


def card(description=DESCRIPTION, author=AUTHOR):
    return parse_finder_card(payload(description, author))[0]


def enrichment(item, **overrides):
    result = dict(card_id=item['card_id'], relevance='ai_dev', summary='虚构工具介绍',
                  items=[dict(name='DemoKit', kind='tool', url='https://demo.invalid/kit')],
                  usefulness='可尝试辅助整理供应商装箱资料，实际效果需要核对',
                  how_to_use='把 skill 放进 skills 目录', confidence='high')
    result.update(overrides)
    return result


def create_source(path, rows=()):
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE messages (
            app_id TEXT, message_id TEXT, group_id TEXT, sender_id TEXT,
            created_at REAL, payload TEXT, status TEXT, rule TEXT, updated_at REAL,
            PRIMARY KEY(app_id,message_id))''')
        connection.executemany('INSERT INTO messages(app_id,message_id,created_at,payload) VALUES (?,?,?,?)', rows)
