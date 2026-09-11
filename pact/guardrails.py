"""Deterministic input controls; never interpret document text as tool authority."""
import re
from starlette.responses import JSONResponse


def inspect_text(text):
    flags = []
    for pattern, reason in [
        (r'ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions', 'Instruction override in document'),
        (r'(?:reveal|print|send|exfiltrate).{0,40}(?:api.?key|password|system prompt)', 'Credential or prompt disclosure instruction'),
        (r'<\|(?:system|assistant|im_start)\|>', 'Model control token in document'),
    ]:
        if re.search(pattern, text, re.I | re.S):
            flags.append(reason)
    if '\x00' in text:
        flags.append('Null bytes in extracted document')
    return flags


class BodyLimit:
    """Bound streamed request bodies even when Content-Length is absent or false."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        header = dict(scope.get('headers', [])).get(b'content-length', b'0')
        try:
            length = int(header)
            if length < 0:
                raise ValueError()
        except ValueError:
            return await JSONResponse({'detail':'Invalid content length'},400)(scope,receive,send)
        if length > self.limit:
            return await JSONResponse({'detail':'Request too large'},413)(scope,receive,send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body = message.get('body', b'')
            size += len(body)
            if size > self.limit:
                return await JSONResponse({'detail':'Request too large'},413)(scope,receive,send)
            chunks.append(body)
            if not message.get('more_body',False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type':'http.request','body':b''.join(chunks),'more_body':False}

        await self.app(scope,replay,send)
