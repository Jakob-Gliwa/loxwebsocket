"""A backend-agnostic websocket double.

``FakeWSTransport`` implements the minimal surface ``LoxWs`` uses from an
aiohttp ``ClientWebSocketResponse`` (documented as ``WSLike`` below). Because
the client only ever touches this contract, the same tests keep working if the
transport is later swapped for ``picows`` - the future adapter just has to
satisfy ``WSLike``.

The double is deliberately faithful about aiohttp's "Concurrent call to
receive() is not allowed" guard: two overlapping reads raise ``RuntimeError``.
That is what lets us prove ``send_command`` routes responses through the
listener's future instead of calling ``receive()`` a second time.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aiohttp import WSMsgType

from .loxone_builders import build_header

_CLOSE_TYPES = (
    WSMsgType.CLOSE,
    WSMsgType.CLOSING,
    WSMsgType.CLOSED,
    WSMsgType.ERROR,
)


@dataclass
class FakeMsg:
    """Stand-in for aiohttp's ``WSMessage`` (only ``.type``/``.data`` are used)."""

    type: WSMsgType
    data: Any = None


@runtime_checkable
class WSLike(Protocol):
    """The websocket surface ``LoxWs`` depends on (aiohttp- and picows-agnostic)."""

    async def send_str(self, data: str) -> None: ...
    async def receive(self) -> Any: ...
    async def close(self) -> None: ...
    @property
    def closed(self) -> bool: ...
    @property
    def close_code(self) -> int | None: ...
    def __aiter__(self) -> Any: ...
    async def __anext__(self) -> Any: ...


class FakeWSTransport:
    def __init__(self, close_code: int | None = None):
        self.sent: list[str] = []
        self.close_calls = 0
        self._incoming: deque[FakeMsg] = deque()
        self._event = asyncio.Event()
        self._closed = False
        self._close_code = close_code
        self._in_flight = 0

    # ------------------------------------------------------------------ #
    # scripting API                                                      #
    # ------------------------------------------------------------------ #
    def queue_msg(self, msg_type: WSMsgType, data: Any = None) -> FakeWSTransport:
        self._incoming.append(FakeMsg(msg_type, data))
        self._event.set()
        return self

    def queue_text(self, data: str) -> FakeWSTransport:
        return self.queue_msg(WSMsgType.TEXT, data)

    def queue_binary(self, data: bytes) -> FakeWSTransport:
        return self.queue_msg(WSMsgType.BINARY, data)

    def queue_header_and_payload(
        self, msg_type: int, payload: bytes | str, *, estimated: bool = False
    ) -> FakeWSTransport:
        """Queue an 8-byte header frame followed by its payload frame."""
        payload_len = len(payload)
        self.queue_binary(build_header(msg_type, payload_len, estimated=estimated))
        if isinstance(payload, (bytes, bytearray)):
            self.queue_binary(bytes(payload))
        else:
            self.queue_text(payload)
        return self

    def queue_close(self, close_code: int | None = 1000) -> FakeWSTransport:
        self._close_code = close_code
        return self.queue_msg(WSMsgType.CLOSED)

    # ------------------------------------------------------------------ #
    # WSLike implementation                                              #
    # ------------------------------------------------------------------ #
    async def _next(self) -> FakeMsg:
        self._in_flight += 1
        try:
            if self._in_flight > 1:
                raise RuntimeError("Concurrent call to receive() is not allowed")
            while not self._incoming:
                if self._closed:
                    return FakeMsg(WSMsgType.CLOSED)
                self._event.clear()
                await self._event.wait()
            return self._incoming.popleft()
        finally:
            self._in_flight -= 1

    async def send_str(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self) -> FakeMsg:
        return await self._next()

    async def close(self) -> None:
        self.close_calls += 1
        self._closed = True
        self._event.set()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_code(self) -> int | None:
        return self._close_code

    def __aiter__(self) -> FakeWSTransport:
        return self

    async def __anext__(self) -> FakeMsg:
        msg = await self._next()
        if msg.type in _CLOSE_TYPES:
            raise StopAsyncIteration
        return msg
