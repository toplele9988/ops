import pytest

from entities_fixtures import batch, entities, entity


@pytest.mark.parametrize('changes,code', [
    ({'entity_type': 'publish'}, 'invalid_entity_type'),
    ({'entity_type': {}}, 'invalid_entity_type'),
    ({'entity_id': ''}, 'invalid_entity_id'),
    ({'entity_id': 'x' * 257}, 'invalid_entity_id'),
    ({'entity_id': '../with space'}, 'invalid_entity_id'),
    ({'observed_at': '2026-11-01T08:00:00'}, 'invalid_entity_observed_at'),
    ({'observed_at': '2026-11-01T08:00:00+08:00'}, 'invalid_entity_observed_at'),
    ({'observed_at': '2026-02-30T08:00:00Z'}, 'invalid_entity_observed_at'),
    ({'observed_at': None}, 'invalid_entity_observed_at'),
    ({'source_page': 'https://message.alibaba.com/orders'}, 'invalid_entity_source_page'),
    ({'source_page': '//example.invalid/path'}, 'invalid_entity_source_page'),
    ({'source_page': '/path\\bad'}, 'invalid_entity_source_page'),
    ({'source_page': '/path\n'}, 'invalid_entity_source_page'),
    ({'collector_version': ''}, 'invalid_collector_version'),
    ({'fields': []}, 'invalid_entity_data'),
    ({'related': []}, 'invalid_entity_related'),
    ({'fields': {'invalid': float('inf')}}, 'invalid_entity_json'),
    ({'fields': {'invalid': '\ud800'}}, 'invalid_entity_json'),
    ({'attachments': ['url']}, 'invalid_entity_attachments'),
    ({'attachments': [{'event_id': 'fictional', 'attachment_id': '../bad'}]}, 'invalid_entity_attachments'),
    ({'attachments': [{'event_id': 'e1_alias', 'attachment_id': 'fictional-file'}]}, 'invalid_entity_attachment_event_id'),
    ({'unexpected': 'bad'}, 'invalid_entity_fields'),
])
def test_invalid_entity_never_writes_partial_batch(store, changes, code):
    with pytest.raises(entities.EntityRejected) as exc:
        store.receive(batch(values=[entity(entity_id='first'), entity(**changes)]))
    assert exc.value.code == code
    assert store.stats()['total_batches'] == 0


@pytest.mark.parametrize('payload,code', [
    ([], 'invalid_entity_batch'), ({}, 'invalid_entity_batch'),
    (batch('', []), 'invalid_entity_batch_id'),
    (batch(values=[]), 'invalid_entity_batch_size'),
    (batch(values=[entity()] * 101), 'invalid_entity_batch_size'),
    (batch(values=[entity(), entity()]), 'duplicate_entity_in_batch'),
    (batch(values=[{}]), 'invalid_entity_fields'),
])
def test_invalid_batch(store, payload, code):
    with pytest.raises(entities.EntityRejected) as exc:
        store.receive(payload)
    assert exc.value.code == code and store.stats()['total_batches'] == 0
