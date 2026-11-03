"""Serve archived attachments (images, video, files) behind the console auth gates.

GET /api/v1/media/{token}: the token is an HMAC-signed reference to one message attachment.
"""
from pathlib import Path
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .inbox.http import authorised, forbidden, recheck
from .media import alibaba, files, tokens
from .wamedia import MediaStore

BASE = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600', 'Accept-Ranges': 'bytes'}


def build(app_module, db_path=files.WECHAT_DB, root=files.ARCHIVE_ROOT, key_path=tokens.KEY_PATH, *,
          alibaba_db_path=alibaba.DB_PATH, alibaba_root=alibaba.ROOT, wa_store=None):
    wa_store = wa_store or MediaStore()

    # lookup returns (path-or-bytes, size, mime, download name, held in memory) or None.
    def lookup(token):
        parts = tokens.read(token, key_path)
        if parts and parts[0] == 'ali':
            found = alibaba.locate(parts, alibaba_db_path, alibaba_root)
            return (*found, False) if found else None
        if isinstance(parts, list) and len(parts) == 2 and parts[0] == 'wa':
            result = wa_store.read(parts[1])
            if not result:
                return None
            row, data = result
            detected = files.sniff(data[:16])
            mime = detected if ((row['kind'] == 'image' and detected.startswith('image/')) or
                                (row['kind'] == 'video' and detected.startswith('video/'))) else 'application/octet-stream'
            return data, len(data), mime, '附件', True
        found = files.locate(parts, db_path, root) if parts else None
        if not found:
            return None
        path, size = found
        return path, size, files.sniff(files.head_of(path)), '附件', False

    async def serve(request):
        if not authorised(request):
            return forbidden(app_module, request)
        try:
            found = await run_in_threadpool(lookup, request.path_params.get('token', ''))
        except Exception:
            found = None
        if not await recheck(request):
            return forbidden(app_module, request)
        if not found:
            return JSONResponse({'error': '文件不在本地或链接已失效'}, status_code=404, headers={'Cache-Control': 'no-store'})
        path, size, mime, name, in_memory = found
        window = files.parse_range(request.headers.get('range'), size)
        if window[0] == 'bad':
            return Response(status_code=416, headers={**BASE, 'Content-Range': f'bytes */{size}'})
        _, start, end = window
        headers = dict(BASE, **{'Content-Length': str(end - start + 1)})
        if not mime.startswith(('image/', 'video/')):
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(name, safe='')
        if window[0] == 'part':
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        if in_memory:
            return Response(path[start:end + 1], status_code=206 if window[0] == 'part' else 200,
                            media_type=mime, headers=headers)
        return StreamingResponse(files.iterate(path, start, end), status_code=206 if window[0] == 'part' else 200,
                                 media_type=mime, headers=headers)

    return [Route('/api/v1/media/{token}', serve, methods=['GET'], name='media')]
