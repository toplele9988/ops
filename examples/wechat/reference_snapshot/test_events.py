"""Synthetic fixtures; may run against snapshot or the same original API."""
import copy
import dataclasses
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest

root=Path(os.environ.get('WECHAT_FIXTURE_SOURCE',Path(__file__).parent)).resolve()
sys.path.insert(0,str(root))
# Avoid executing a production package __init__ while testing source in place.
package=types.ModuleType('video_knowledge');package.__path__=[str(root/'video_knowledge')];sys.modules['video_knowledge']=package
import wtapi_events as events
from video_knowledge import events as video

def text_event(group=True):
    return {'TypeName':'AddMsg','Appid':'app-fixture','Wxid':'owner-fixture','Data':{'MsgType':1,'FromUserName':{'string':'group-fixture@chatroom' if group else 'buyer-fixture'},'ToUserName':{'string':'owner-fixture'},'NewMsgId':123,'CreateTime':1700000000,'Content':{'string':'buyer-fixture:\nSynthetic request' if group else 'Synthetic request'},'MsgSource':{'string':'<msgsource><atuserlist>member-one,member-two,member-one</atuserlist></msgsource>'}}}

class EventsTests(unittest.TestCase):
    def test_group_message_and_mentions(self):
        row=events.normalize_group_message(text_event())
        self.assertEqual((row.app_id,row.group_id,row.sender_id,row.text,row.at_ids),('app-fixture','group-fixture@chatroom','buyer-fixture','Synthetic request',('member-one','member-two')))
    def test_private_message(self):
        row=events.normalize_private_message(text_event(False))
        self.assertEqual((row.app_id,row.sender_id,row.message_id,row.text),('app-fixture','buyer-fixture','123','Synthetic request'))
    def test_wrong_recipient_and_system_sender_are_rejected(self):
        data=text_event(False);data['Data']['ToUserName']={'string':'someone-else'}
        self.assertIsNone(events.normalize_private_message(data))
        data=text_event(False);data['Data']['FromUserName']={'string':'system'}
        self.assertIsNone(events.normalize_private_message(data))
    def test_wrong_type_and_missing_sender_are_rejected(self):
        data=text_event();data['Data']['MsgType']=True;self.assertIsNone(events.normalize_group_message(data))
        data=text_event();data['Data']['Content']={'string':':\nSynthetic'};self.assertIsNone(events.normalize_group_message(data))
    def test_untrusted_mention_xml_is_ignored(self):
        data=text_event();data['Data']['MsgSource']={'string':'<!DOCTYPE x [<!ENTITY x "fake">]><msgsource><atuserlist>&x;</atuserlist></msgsource>'}
        self.assertEqual(events.normalize_group_message(data).at_ids,())
    def test_image_descriptor_is_not_text_or_download(self):
        data=text_event(False);data['Data']['MsgType']=3;data['Data']['Content']={'string':'<msg><img length="20"/></msg>'}
        result=events.normalize_owner_media_message(data)
        self.assertEqual(result.attachments[0]['kind'],'image');self.assertFalse(result.attachments[0]['download_supported']);self.assertEqual(result.text,'[图片]')
    def test_reference_rejects_entities(self):
        result=events.read_reference_xml('<!DOCTYPE x [<!ENTITY x "fake">]><msg>&x;</msg>')
        self.assertEqual(result['status'],'unsupported_wechat_reference')
    @unittest.skipUnless(importlib.util.find_spec('defusedxml'), 'defusedxml unavailable')
    def test_private_reference_uses_defused_xml(self):
        data=text_event(False);data['Data']['MsgType']=49;data['Data']['Content']={'string':'<!DOCTYPE x [<!ENTITY x "fake">]><msg>&x;</msg>'}
        self.assertIsNone(events.private_reference(data));self.assertIsNone(events.normalize_private_message(data))
    def test_finder_parses_synthetic_metadata(self):
        xml='<msg><appmsg><type>51</type><title>Fixture title</title><finderFeed><desc>Fixture description</desc><nickname>Fixture author</nickname><mediaList><media><url>https://media.fixture.invalid/sample</url></media></mediaList></finderFeed></appmsg></msg>'
        row=video.parse_finder_xml(xml)
        self.assertEqual(row.model_metadata(),{'kind':'finder','title':'Fixture title','description':'Fixture description','author':'Fixture author'})
        self.assertEqual(row.download_status,'finder_download_contract_unverified')
    def test_finder_rejects_entity_and_wrong_type(self):
        with self.assertRaises(video.VideoError):video.parse_finder_xml('<!DOCTYPE x><msg/>')
        with self.assertRaises(video.VideoError):video.parse_finder_xml('<msg><appmsg><type>6</type></appmsg></msg>')
    def test_safe_text_removes_fake_identity_and_url(self):
        value=video.safe_text('Synthetic text https://media.fixture.invalid/token?secret=fake')
        self.assertNotIn('fixture.invalid',value);self.assertIn('[链接或路径已省略]',value)

if __name__=='__main__':unittest.main()
