"""No production imports, configuration, databases, models or fixed ports."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / 'deploy' / 'icbu'
if str(DEPLOY) not in sys.path:
    sys.path.insert(0, str(DEPLOY))


def load_receiver(path=DEPLOY / 'icbu_inbound_receiver.py', name='p2b_receiver'):
    def module(name, **attributes):
        result = ModuleType(name)
        result.__dict__.update(attributes)
        return result

    deps = {
        'icbu_official_imports': module('icbu_official_imports', ImportArchive=object,
                                      ImportRejected=ValueError, IMPORT_PATH='/api/inbound/alibaba/imports'),
        'icbu_probe_download': module('icbu_probe_download', PROBE_DOWNLOAD_PATH='/api/probe',
                                     ProbeDownloadRejected=ValueError, serve_probe=lambda _: None),
        'icbu_probe_heartbeat': module('icbu_probe_heartbeat', HEARTBEAT_PATH='/api/heartbeat',
                                      HeartbeatRejected=ValueError, HeartbeatStore=object,
                                      read_payload=lambda *_: {}, server_clock=lambda: {}),
        'icbu_live_drafts': module('icbu_live_drafts', checked_provenance=lambda value: value),
        'icbu_operations': module('icbu_operations', Operations=object, API_PREFIX='/api/operations/alibaba/'),
    }
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, deps):
        spec.loader.exec_module(result)
    return result


def message(**changes):
    payload = dict(account='Amy', conversation_id='tm:fictional-customer', buyer_name='虚构买家',
                   content='虚构资料', country='虚构地区', url='https://message.alibaba.com/',
                   message_id='fictional-message-1', event_id='fictional-event-1',
                   sent_at='2026-10-30T10:00:00Z', observed_at='2026-10-30T10:00:01Z',
                   history=True, role='buyer', probe_version='2.0.0')
    return dict(payload, **changes)


def attachment(body=b'fictional attachment\n', **changes):
    import hashlib
    value = dict(attachment_id='fictional-file', kind='file', filename='fictional.txt',
                 mime='text/plain', size=len(body), sha256=hashlib.sha256(body).hexdigest())
    return dict(value, **changes)
