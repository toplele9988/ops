import json
import re

import pytest

from entities_fixtures import ROOT, entities


def test_documented_schema_and_fictional_example_are_valid_and_accepted(tmp_path):
    jsonschema = pytest.importorskip('jsonschema')
    blocks = re.findall(r'```json\n(.*?)\n```', (ROOT / 'docs/icbu-entities-contract.md').read_text(), re.S)
    schema, example = map(json.loads, blocks)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(example, schema, format_checker=jsonschema.FormatChecker())
    store = entities.EntityStore(tmp_path / 'entities.sqlite3')
    assert store.receive(example)['entity_count'] == 2
    assert store.receive(example)['duplicate']
    # These provenance and identity mistakes must fail in both contract and implementation.
    for key, value in [('source_page', 'https://example.invalid/path'), ('entity_id', 'bad\\id')]:
        changed = json.loads(json.dumps(example))
        changed['entities'][0][key] = value
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(changed, schema)
        with pytest.raises(entities.EntityRejected):
            entities.validate_batch(changed)
