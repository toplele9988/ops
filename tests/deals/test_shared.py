from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from console import channels


class SharedInboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'inbound.sqlite3'

    def test_same_effective_paths_and_defaults(self):
        a = channels.build_inbox(self.path)
        self.assertIs(a, channels.shared_inbox(str(self.path)))
        self.assertIs(a, channels.shared_inbox(self.path.parent / '.' / self.path.name,
            whatsapp_config_path=self.path.parent / 'disabled-whatsapp.json'))
        self.assertIsNot(a, channels.shared_inbox(self.path.parent / 'other.sqlite3'))
        for fields in ({'wechat_path': self.path.parent / 'other'}, {'fetch': lambda *a: {}},
                       {'clock': lambda: 0}, {'stat_fn': lambda *a: None}, {'group_gid': 987},
                       {'include_roles': ('customer',)}):
            self.assertIsNot(a, channels.shared_inbox(self.path, **fields))
        self.assertFalse(self.path.exists())

    def test_concurrent_build_once(self):
        barrier = threading.Barrier(16)
        def call(_):
            barrier.wait()
            return channels.shared_inbox(self.path)
        with patch.object(channels, '_build_inbox', wraps=channels._build_inbox) as construct:
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(call, range(16)))
        self.assertEqual(construct.call_count, 1)
        self.assertTrue(all(r is results[0] for r in results))

    def test_config_symlink_is_not_resolved_past_adapter_safety_check(self):
        config = self.path.parent / 'fictional-config.json'
        config.write_text('{}')
        link = self.path.parent / 'fictional-link.json'
        link.symlink_to(config)
        inbox = channels.shared_inbox(self.path, whatsapp_config_path=link)
        self.assertEqual(inbox.adapters['whatsapp'].path, link)
