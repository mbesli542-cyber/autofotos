"""Request guard – pure ASGI middleware that runs before FastAPI reads a body.

- Bearer authentication for every path except the public ones (/health):
  unauthenticated requests are rejected without reading their body.
- Request body limit: a too large Content-Length is rejected up front and
  streamed (chunked) bodies are counted while they are read → 413.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class _BodyTooLarge(Exception):
    pass


def _error_messages(status: int, code: str, message: str) -> list[Message]:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return [
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        },
        {"type": "http.response.body", "body": body},
    ]


def is_authorized(authorization: bytes | str | None, api_key: str | None) -> bool:
    """Constant-time check of an "Authorization: Bearer <key>" header (no key = open)."""
    if not api_key:
        return True
    if authorization is None:
        return False
    given = authorization.encode() if isinstance(authorization, str) else authorization
    return hmac.compare_digest(given, f"Bearer {api_key}".encode())


class RequestGuard:
    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        api_key: str | None,
        max_body_bytes: int,
        public_paths: Iterable[str] = ("/health",),
    ) -> None:
        self.app = app
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        self.public_paths = frozenset(public_paths)

    async def _reply(self, send: Send, status: int, code: str, message: str) -> None:
        for item in _error_messages(status, code, message):
            await send(item)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.public_paths:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        if not is_authorized(headers.get(b"authorization"), self.api_key):
            await self._reply(send, 401, "unauthorized", "Nicht autorisiert.")
            return

        length = headers.get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                await self._reply(send, 400, "invalid_request", "Ungültige Anfrage.")
                return
            if declared > self.max_body_bytes:
                await self._reply(send, 413, "too_large", "Die Datei ist zu groß.")
                return

        received = 0
        exceeded = False
        started = False

        async def guarded_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                # The app reacts to the aborted body with its own error – answer 413 instead.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await self._reply(send, 413, "too_large", "Die Datei ist zu groß.")
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except _BodyTooLarge:
            if not started:
                await self._reply(send, 413, "too_large", "Die Datei ist zu groß.")
