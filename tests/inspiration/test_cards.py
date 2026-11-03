import json
import unittest

from console.inspiration.cards import parse_finder_card
from inspiration_fixtures import AUTHOR, DESCRIPTION, card, payload


class CardTests(unittest.TestCase):
    def test_current_xml_and_legacy_descriptor_have_same_fields_and_identity(self):
        old = parse_finder_card(payload())[0]
        current = parse_finder_card(payload(xml=True))[0]
        self.assertEqual(old, current)
        self.assertEqual(old['author'], AUTHOR)
        self.assertEqual(old['description'], DESCRIPTION)
        self.assertEqual(old['tags'], ['AI', '开源'])
        self.assertEqual(old['urls'], ['https://demo.invalid/kit'])
        self.assertNotIn('#', old['text'])
        self.assertRegex(old['card_id'], r'^[0-9a-f]{16}$')

    def test_cdata_literal_newlines_entities_and_zero_width(self):
        text = r'虚构 DemoKit\n用法：复制到目录 &amp; 保存&#33; #AI #AI #开源'
        parsed = parse_finder_card(payload(text + '\u200b\ufeff', author='示例\u200b号甲', xml=True))[0]
        self.assertEqual(parsed['author'], AUTHOR)
        self.assertIn('\n用法', parsed['description'])
        self.assertIn('& 保存!', parsed['text'])
        self.assertNotIn('CDATA', parsed['text'])
        self.assertNotIn('\\n', parsed['text'])
        self.assertNotIn('\u200b', parsed['text'])
        self.assertEqual(parsed['tags'], ['AI', '开源'])
        wrapped = parse_finder_card(payload('<![CDATA[示例 &amp; 文字\\n#AI]]>'))[0]
        self.assertEqual(wrapped['text'], '示例 & 文字')

    def test_topics_order_punctuation_and_url_fragments(self):
        parsed = card('虚构#AI，#开源 #AI https://demo.invalid/a#section '
                      'https://demo.invalid/b?q=1&amp;x=2。 https://demo.invalid/a#section')
        self.assertEqual(parsed['tags'], ['AI', '开源'])
        self.assertEqual(parsed['urls'], ['https://demo.invalid/a#section', 'https://demo.invalid/b?q=1&x=2'])
        self.assertIn('https://demo.invalid/a#section', parsed['text'])

    def test_repeated_forwards_whitespace_and_descriptor_versions_share_id(self):
        first = card('DemoKit   虚构项目 #AI #AI')
        second = parse_finder_card(payload(' DemoKit\\n虚构项目 #AI ', xml=True))[0]
        self.assertEqual(first['card_id'], second['card_id'])
        self.assertNotEqual(first['card_id'], card('另一个虚构项目 #AI')['card_id'])
        self.assertNotEqual(first['card_id'], card('DemoKit 虚构项目 #AI', '示例号乙')['card_id'])

    def test_multiple_attachments_and_missing_fields(self):
        attachments = [None, {}, {'appmsg_type': 51, 'xml': '<bad'},
                       {'finder_author': '示例号甲'}, {'finder_description': '虚构文字'},
                       {'finder_author': '', 'finder_description': ''},
                       {'appmsg_type': 51, 'xml': '<finderFeed><desc>虚构 XML</desc></finderFeed>'}]
        parsed = parse_finder_card(json.dumps({'attachments': attachments}))
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]['text'], '')
        self.assertEqual(parsed[1]['author'], '')
        self.assertEqual(parsed[2]['text'], '虚构 XML')

    def test_non_cards_invalid_json_and_wrong_message_type_are_ignored(self):
        for raw in ('bad', 'null', '[]', '{}', None, '{"attachments":{}}',
                    json.dumps({'msg_type': 1, 'attachments': [{'finder_description': '虚构'}]}),
                    json.dumps({'attachments': [{'appmsg_type': 5, 'xml': '<finderFeed/>'}]})):
            with self.subTest(raw=raw):
                self.assertEqual(parse_finder_card(raw), [])

    def test_xml_entities_are_not_expanded(self):
        raw = '<!DOCTYPE x [<!ENTITY a "虚构">]><finderFeed><desc>&a;</desc></finderFeed>'
        self.assertEqual(parse_finder_card(json.dumps({'attachments': [{'appmsg_type': 51, 'xml': raw}]})), [])


if __name__ == '__main__':
    unittest.main()
