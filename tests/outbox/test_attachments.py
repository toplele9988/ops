"""Attachment sends use fictional files, contacts and injected bridge calls."""
import base64
import unittest
from unittest.mock import patch
from outbox_fixtures import IsolatedTest, form


class AttachmentTests(IsolatedTest):
    def create(self, **kw):
        fields = dict(filename='虚构资料.pdf', mimetype='application/pdf',
                      data=base64.b64encode(b'fictional file bytes').decode(), request_id='candidate-0001')
        fields.update(kw)
        return self.service.create_attachment(self.key, fields)

    def send_attachment(self, candidate='1', request='attachment-send-0001', **kw):
        fields = dict(candidate_id=candidate, request_id=request, confirm='1')
        fields.update(kw)
        return self.service.send_attachment(self.key, fields)

    def install_gateway(self, answer=None):
        calls = []
        def post(url, headers, body, timeout):
            calls.append((url, body))
            if isinstance(answer, Exception):
                raise answer
            return answer or (200, dict(idempotency_key=body['idempotency_key'], state='accepted', error_code=None))
        self.bridge.post = post
        return calls

    def test_candidate_deduplication_and_validation(self):
        result, code = self.create()
        self.assertEqual(code, 200)
        self.assertEqual(result['candidate']['size'], len(b'fictional file bytes'))
        self.assertEqual(self.create(), (result, code))
        self.assertEqual(self.create(filename='../unsafe.pdf')[1], 400)
        self.assertEqual(self.create(mimetype='application/x-executable')[1], 400)
        self.assertEqual(self.create(data='!invalid!')[1], 400)
        with patch('console.outbox.attachments.MAX_SIZE', 2):
            self.assertEqual(self.create(request_id='candidate-0002')[1], 400)

    def test_disabled_and_group_are_rejected(self):
        self.create()
        calls = self.install_gateway()
        self.assertEqual(self.send_attachment()[1], 403)
        group = self.hub.add(chat='fictional-group@g.us')
        self.enable()
        self.assertEqual(self.service.create_attachment(group, dict(filename='x.pdf', mimetype='application/pdf',
                         data='YQ==', request_id='group-candidate-01'))[1], 400)
        self.assertEqual(self.service.send_attachment(group, dict(candidate_id='1', request_id='group-send-01',
                         confirm='1'))[1], 400)
        self.assertEqual(calls, [])

    def test_confirmed_send_is_idempotent_and_candidate_is_one_shot(self):
        self.enable()
        self.create()
        calls = self.install_gateway()
        self.assertEqual(self.send_attachment(confirm='0')[1], 400)
        first, code = self.send_attachment()
        self.assertEqual(code, 200)
        self.assertEqual(first['message']['state'], 'sent')
        self.assertEqual(self.send_attachment()[0], first)
        self.assertEqual(self.send_attachment(request='attachment-send-0002')[1], 409)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith('/api/sendFile'))
        self.assertEqual(base64.b64decode(calls[0][1]['file']['data']), b'fictional file bytes')
        self.assertFalse(calls[0][1]['auto_send'])

    def test_uncertain_result_never_retries(self):
        self.enable()
        self.create()
        calls = self.install_gateway(TimeoutError())
        self.assertEqual(self.send_attachment()[0]['message']['state'], 'unknown')
        self.assertEqual(self.send_attachment()[0]['message']['state'], 'unknown')
        self.assertEqual(self.send_attachment(request='attachment-send-0002')[1], 409)
        self.assertEqual(len(calls), 1)

    def test_new_recipient_requires_second_explicit_confirmation(self):
        self.enable()
        self.create()
        calls = []
        def post(url, headers, body, timeout):
            calls.append(url)
            if url.endswith('/api/recipients/bind'):
                return 200, {'bound': True}
            if len([x for x in calls if x.endswith('/api/recipients/bind')]) == 0:
                return 409, {'error': 'recipient_not_bound'}
            return 200, dict(idempotency_key=body['idempotency_key'], state='accepted', error_code=None)
        self.bridge.post = post
        result, code = self.send_attachment()
        self.assertEqual(code, 200)
        self.assertTrue(result['needs_new_recipient_confirmation'])
        self.assertEqual(self.send_attachment(request='attachment-send-0002')[1], 409)
        result, code = self.send_attachment(request='attachment-send-0002', confirm_new_recipient='1')
        self.assertEqual(code, 200)
        self.assertEqual(result['message']['state'], 'sent')
        self.assertEqual(len([x for x in calls if x.endswith('/api/sendFile')]), 3)
        self.assertEqual(len([x for x in calls if x.endswith('/api/recipients/bind')]), 1)

    def test_request_id_owned_by_text_is_not_attachment(self):
        self.enable()
        self.create()
        self.install_gateway()
        self.service.send(self.key, form(request_id='attachment-send-0001'))
        self.assertEqual(self.send_attachment()[1], 409)

    def test_reservation_race_cannot_confuse_existing_text_with_attachment(self):
        self.enable()
        row, new, rejection, _ = self.service.store.reserve(self.key, 'fictional-contact@c.us',
            '普通文本', 'fictional-race-01', None, self.now)
        self.assertTrue(new)
        _, new, rejection, _ = self.service.store.reserve(self.key, 'fictional-contact@c.us',
            '附件候选 #1', 'fictional-race-01', None, self.now, attachment_marker='附件候选 #1')
        self.assertFalse(new)
        self.assertEqual(rejection, 'request_id_conflict')

    def test_image_and_video_contract(self):
        self.enable()
        calls = self.install_gateway()
        self.create(filename='x.png', mimetype='image/png', request_id='candidate-image-01')
        self.send_attachment()
        self.now += 61
        self.create(filename='x.mp4', mimetype='video/mp4', request_id='candidate-video-01')
        self.send_attachment(candidate='2', request='attachment-send-0002')
        self.assertTrue(calls[0][0].endswith('/api/sendImage'))
        self.assertTrue(calls[1][0].endswith('/api/sendVideo'))

if __name__ == '__main__':
    unittest.main()
