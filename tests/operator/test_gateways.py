"""Real adapter logic with fake configured clients; no network or credentials."""
from types import SimpleNamespace
import pytest
from console.operator.gateways import WhatsAppSender, WeChatSender, ChannelBridge
from console.inbox.model import make_key


@pytest.mark.parametrize('actor,expected', [('operator_auto','model_auto'), ('fictional-owner','approved_operator')])
def test_waha_identity_and_auto_scope_are_preserved(actor, expected):
    packets = []
    class Client:
        get, post = object(), object()
        def _configuration(self):
            return 'fictional-credential', 'fictional-session', 'http://127.0.0.1:1'
        def _call(self, transport, api, path, headers, body=None):
            if path == '/api/binding':
                return 200, dict(session='fictional-session', account_id='fictional-account@c.us')
            assert path == '/api/sendText'
            packets.append(body)
            return 200, dict(state='accepted', idempotency_key=body['idempotency_key'])
        def _outcome(self, status, result, idem):
            assert result['idempotency_key'] == idem
            return 'sent', None
    key = make_key('conversation','whatsapp','fictional-session','fictional-recipient@c.us')
    sender = WhatsAppSender(Client(),lambda _: dict(session='fictional-session',account_id='fictional-account@c.us',chat_id='fictional-recipient@c.us'))
    assert sender.send(key,'Fictional text','fictional-reservation',actor)=='sent'
    assert packets[0]['actor']==expected
    assert packets[0]['auto_send']==(actor=='operator_auto')
    assert len(packets)==1


@pytest.mark.parametrize('target', [dict(session='wrong-account'), dict(session='session',account_id='fictional-account@c.us',chat_id='wrong-recipient@c.us')])
def test_waha_mismatched_account_never_posts(target):
    client = SimpleNamespace(_configuration=lambda:('fictional', 'session', 'http://127.0.0.1:1'))
    sender = WhatsAppSender(client,lambda _:target)
    key = make_key('conversation','whatsapp','fictional-session','fictional-recipient@c.us')
    assert sender.send(key,'Fictional text','fictional-id','operator_auto')=='failed'


@pytest.mark.parametrize('outcome', ['sent','unknown'])
def test_wechat_calls_existing_send_text_once(outcome):
    calls=[]
    def send(app_id, to_wxid, content):
        calls.append((app_id,to_wxid,content))
        if outcome=='unknown':
            raise TimeoutError()
        return dict(toWxid=to_wxid,newMsgId='12345')
    key=make_key('conversation','wechat','fictional-channel','fictional-wxid')
    sender=WeChatSender(SimpleNamespace(send_text=send),resolve=lambda _:dict(app_id='fictional-app',to_wxid='fictional-wxid'),allowed=lambda k,a:True)
    assert sender.send(key,'Fictional text','fictional-id','operator_auto')==outcome
    assert len(calls)==1


def test_wechat_wrong_recipient_and_alibaba_never_call_sender():
    client=SimpleNamespace(send_text=lambda *a:pytest.fail('must not call sender'))
    key=make_key('conversation','wechat','fictional-channel','fictional-wxid')
    sender=WeChatSender(client,resolve=lambda _:dict(app_id='fictional-app',to_wxid='wrong-recipient'),allowed=lambda k,a:True)
    assert sender.send(key,'Fictional text','fictional-id','operator_auto')=='failed'
    bridge=ChannelBridge(senders={'alibaba':sender}, domain=None)
    assert not bridge.available('alibaba')
