"""Reject unauthorized/oversized bodies before multipart parsing touches disk."""
import asyncio
import time
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from backend.config import MAX_UPLOAD_SIZE_BYTES, MAX_ARTWORK_SIZE_BYTES, DATA_DIR
from backend.locking import file_lock


class RequestLimitsMiddleware:
    def __init__(self, app, access_check):
        self.app = app
        self.access_check = access_check

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope['path']
        limit = 256 * 1024
        upload = path == '/api/upload'
        artwork = path == '/api/artwork' and scope['method'] == 'POST'
        storage_write = path in ('/api/upload', '/api/save', '/api/artwork', '/api/suno/apply-artwork', '/api/session')
        if storage_write:
            # Include a small allowance for multipart headers, not an unbounded body.
            if upload or artwork:
                limit = (MAX_UPLOAD_SIZE_BYTES if upload else MAX_ARTWORK_SIZE_BYTES) + 64 * 1024
            try:
                self.access_check(request)
                if upload:
                    from backend.security import upload_rate_limiter
                    if not upload_rate_limiter.is_allowed(upload_rate_limiter.get_client_ip(request)):
                        raise HTTPException(429, 'Too many uploads. Please try again later.')
            except HTTPException as exc:
                return await JSONResponse({'detail': exc.detail}, status_code=exc.status_code)(scope, receive, send)
            except Exception:
                return await JSONResponse({'detail': 'Account storage unavailable.'}, status_code=503)(scope, receive, send)
        length = request.headers.get('content-length')
        if length is not None:
            try:
                if int(length) < 0 or int(length) > limit:
                    raise ValueError
            except ValueError:
                return await JSONResponse({'detail': 'Request body exceeds allowed size.'}, status_code=413)(scope, receive, send)
        consumed = 0
        deadline = time.monotonic() + 300
        timed_out = False
        async def limited_receive():
            nonlocal consumed, timed_out
            try:
                message = await asyncio.wait_for(receive(), timeout=max(0, min(30, deadline - time.monotonic())))
            except TimeoutError:
                timed_out = True
                if upload or artwork:
                    from starlette.formparsers import MultiPartException
                    raise MultiPartException('Request timed out.') from None
                raise HTTPException(408, 'Request timed out.') from None
            if message['type'] == 'http.request':
                consumed += len(message.get('body', b''))
                if consumed > limit:
                    if upload or artwork:
                        from starlette.formparsers import MultiPartException
                        raise MultiPartException('Request body exceeds allowed size.')
                    raise HTTPException(413, 'Request body exceeds allowed size.')
            return message
        rejected = False
        async def guarded_send(message):
            nonlocal rejected
            if consumed > limit or timed_out:
                if not rejected:
                    rejected = True
                    await JSONResponse({'detail': 'Request timed out.' if timed_out else 'Request body exceeds allowed size.'}, status_code=408 if timed_out else 413)(scope, receive, send)
                return
            await send(message)

        # One cross-process storage writer bounds spool use and save reservations.
        if storage_write:
            lease = file_lock(DATA_DIR / '.storage-write.lock', blocking=False)
            try:
                lease.__enter__()
            except BlockingIOError:
                return await JSONResponse({'detail': 'Another upload is in progress. Please retry.'}, status_code=503)(scope, receive, send)
            try:
                return await self.app(scope, limited_receive, guarded_send)
            finally:
                lease.__exit__(None, None, None)
        return await self.app(scope, limited_receive, guarded_send)
