"""Local synthetic demo only: no real backend or Google API access."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Gmail 接入演示</title>
<style>body{font:16px system-ui;background:#f4f7fb;color:#1e293b;margin:0}main{max-width:680px;margin:12vh auto;background:white;padding:32px;border-radius:18px;box-shadow:0 14px 40px #182b4c18}h1{margin:0 0 8px}p{line-height:1.6;color:#526176}button{border:0;border-radius:8px;background:#315bd4;color:white;padding:12px 18px;margin:6px 6px 6px 0;cursor:pointer}#status{padding:16px;background:#f1f5ff;border-radius:10px;white-space:pre-wrap}</style>
<main><h1>Gmail 接入演示</h1><p>仅使用虚构数据。真实邮箱尚未连接；此页面不会发送邮件。</p><div id="status">正在读取状态…</div><p><button data-action="sync">同步一次</button><button data-action="pause">暂停同步</button><button data-action="resume">核验并恢复</button></p><p>授权失效时需要到 Google 重新授权，核验邮箱一致后才恢复。</p></main>
<script>const token="TOKEN_PLACEHOLDER";async function refresh(){let r=await fetch('/api/status');let d=await r.json();document.querySelector('#status').textContent='邮箱：'+d.account+'\\n同步状态：'+d.state+'\\n凭据文件：'+(d.credential_file_present?'已存在（仍需核验）':'未配置')+'\\n最近结果：'+d.last_result}document.querySelectorAll('button').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await fetch('/api/'+b.dataset.action,{method:'POST',headers:{'X-Demo-CSRF':token}});await refresh()}finally{b.disabled=false}});refresh()</script></html>'''


def make_server(scheduler, port=0, *, demo=True):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, content_type):
            raw = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, '{}', 'application/json')
            if self.path == '/':
                page = PAGE.replace('TOKEN_PLACEHOLDER', token)
                if not demo:
                    page = page.replace('Gmail 接入演示', 'Gmail 接入管理').replace('仅使用虚构数据。真实邮箱尚未连接；此页面不会发送邮件。', '该入口仅用于管理收信同步，不提供邮件发送。')
                return self.respond(200, page, 'text/html; charset=utf-8')
            if self.path == '/api/status':
                return self.respond(200, json.dumps(scheduler.status()), 'application/json')
            return self.respond(404, '{}', 'application/json')

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.valid_host() or self.headers.get('Origin') != origin or not secrets.compare_digest(self.headers.get('X-Demo-CSRF', ''), token):
                return self.respond(403, '{}', 'application/json')
            operations = {'/api/sync': scheduler.run_once, '/api/pause': scheduler.pause, '/api/resume': scheduler.resume}
            if self.path not in operations:
                return self.respond(404, '{}', 'application/json')
            return self.respond(200, json.dumps(operations[self.path]()), 'application/json')
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    import os
    import tempfile
    from pathlib import Path
    from gmail_sync import API
    from runtime import GmailBackend, Scheduler

    class FakeTransport:
        def get(self, path, params):
            if path == API + '/profile':
                return {'emailAddress': 'demo@example.test', 'historyId': '100'}
            if path == API + '/messages':
                return {'messages': []}
            if path == API + '/history':
                return {'historyId': '100'}
            raise ValueError('unexpected_synthetic_path')

    with tempfile.TemporaryDirectory(prefix='gmail-demo-') as temp:
        root = Path(temp)
        key = root / 'demo.key'
        fd = os.open(key, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(os.urandom(32))
        scheduler = Scheduler(GmailBackend(root, 'demo@example.test', key, FakeTransport), 30)
        server = make_server(scheduler)
        scheduler.start()
        print(f'仅模拟数据：http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            scheduler.stop()
