"""The fake-auth rehearsal must mount login routes without authorizing WAHA writes."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from rehearsal_isolation import isolate_builders

import asyncio
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode


@unittest.skipIf(importlib.util.find_spec('starlette') is None, 'starlette not installed')
class WhatsAppRehearsalTests(unittest.TestCase):
    def test_automounted_routes_keep_real_session_writes_disabled(self):
        from console import (v3_whatsapp_login_api as api, v3_quotes_api, v3_roles_api,
                             v3_directory_api, v3_audit_api, v3_extract_api, v3_fulfil_api,
                             v3_icbu_api, v3_listing_api, v3_lang_api, v3_pipeline_api)
        from console.channels.whatsapp import CONFIG_PATH

        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('network forbidden')))
            stack.enter_context(patch.dict('os.environ', {
                'IMGW_REHEARSAL': '1', 'REAL_ARCHIVE': str(Path(tmp) / 'missing-fictional.sqlite3')}))
            path = Path(__file__).resolve().parents[2] / 'scripts' / 'rehearsal_server.py'
            spec = importlib.util.spec_from_file_location('whatsapp_rehearsal_test', path)
            rehearsal = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rehearsal)
            isolate_builders(stack, rehearsal, 'v3_whatsapp_login_api')
            service = Mock()
            build = api.build

            def isolated_build(gate, archive_path, **options):
                self.assertEqual(Path(archive_path), Path(tmp) / 'archive.sqlite3')
                self.assertIs(options['allow_writes'], False)
                self.assertEqual(options['whatsapp_config_path'], CONFIG_PATH)
                return build(gate, archive_path, service=service, **options)

            mounted = stack.enter_context(patch.object(api, 'build', side_effect=isolated_build))
            routes = rehearsal.build_routes(tmp)
            mounted.assert_called_once()
            self.assertEqual({r.path for r in routes}, {
                '/api/v1/system/whatsapp/' + action for action in ('status', 'qr', 'start', 'logout')})

            for action in ('start', 'logout'):
                fields = {'confirm': '1', 'csrf': 'fictional-csrf'}
                if action == 'logout':
                    fields['phrase'] = '退出登录'
                body = urlencode(fields).encode()

                async def read_body():
                    return body

                request = SimpleNamespace(
                    state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner'),
                        session=SimpleNamespace(authenticated=True), password_recheck=lambda: True,
                        csrf='fictional-csrf', form=None),
                    headers={'content-type': 'application/x-www-form-urlencoded', 'content-length': str(len(body))},
                    body=read_body, path_params={}, query_params={})
                route = next(r for r in routes if r.path.endswith('/' + action))
                result = asyncio.run(route.endpoint(request))
                self.assertEqual(result.status_code, 403)
            service.write.assert_not_called()
