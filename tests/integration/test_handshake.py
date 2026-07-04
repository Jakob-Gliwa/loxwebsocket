"""Full async_init handshake against the scripted websocket double.

Exercises the doc p.7 flow: keyexchange -> session-key check -> token
acquisition/use -> optional enable-updates -> CONNECTED, plus the failure
cleanup path.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from support.crypto_vectors import KNOWN_HMAC_KEY_HEX
from support.loxone_builders import ll_response

import loxwebsocket.const as c
from loxwebsocket.lxtoken import LxToken

pytestmark = pytest.mark.asyncio


class _FakeSession:
    def __init__(self, ws, **kwargs):
        self._ws = ws
        self.closed = False
        self.ws_connect_url = None
        self.ws_connect_kwargs = None

    async def ws_connect(self, url, **kwargs):
        self.ws_connect_url = url
        self.ws_connect_kwargs = kwargs
        return self._ws

    async def close(self):
        self.closed = True


def _wire_session(monkeypatch, fake_ws) -> list[_FakeSession]:
    """Patch ClientSession and return the list of sessions it creates."""
    created: list[_FakeSession] = []

    def factory(**kwargs):
        session = _FakeSession(fake_ws, **kwargs)
        created.append(session)
        return session

    monkeypatch.setattr("loxwebsocket.lox_ws_api.aiohttp.ClientSession", factory)
    return created


def _keyexchange_ok(fake_ws):
    fake_ws.queue_header_and_payload(0, ll_response(code="200", value="serverkey"))


class TestAsyncInitAcquireToken:
    async def test_full_handshake_acquires_token(self, client, fake_ws, monkeypatch):
        client._token = LxToken()  # empty -> acquire path
        client._receive_updates = True
        client._encryption_handler.generate_session_key = AsyncMock(
            return_value="SESSIONKEY"
        )
        created = _wire_session(monkeypatch, fake_ws)

        _keyexchange_ok(fake_ws)
        fake_ws.queue_header_and_payload(
            0,
            json.dumps(
                {"LL": {"value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "s", "hashAlg": "SHA256"}}}
            ),
        )
        fake_ws.queue_header_and_payload(
            0, ll_response(value={"token": "TOK", "validUntil": 10**9})
        )
        fake_ws.queue_header_and_payload(0, ll_response(code="200"))  # enable-updates

        result = await asyncio.wait_for(client.async_init(), timeout=2)

        assert result is True
        assert client.state == "CONNECTED"
        assert client._token.token == "TOK"
        assert client._token.valid_until == 10**9
        assert client._ws is fake_ws
        assert any(s.startswith(c.CMD_KEY_EXCHANGE) for s in fake_ws.sent)
        # doc p. 7 step 3b: the ws is opened with the "remotecontrol" subprotocol.
        assert created[0].ws_connect_kwargs["protocols"] == ("remotecontrol",)
        assert created[0].ws_connect_url.endswith("/ws/rfc6455")

    async def test_receive_updates_false_skips_enable(
        self, client, fake_ws, monkeypatch
    ):
        client._token = LxToken()
        client._receive_updates = False
        client._encryption_handler.generate_session_key = AsyncMock(
            return_value="SESSIONKEY"
        )
        _wire_session(monkeypatch, fake_ws)

        _keyexchange_ok(fake_ws)
        fake_ws.queue_header_and_payload(
            0,
            json.dumps(
                {"LL": {"value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "s", "hashAlg": "SHA1"}}}
            ),
        )
        fake_ws.queue_header_and_payload(
            0, ll_response(value={"token": "TOK", "validUntil": 10**9})
        )
        # No enable-updates frames queued: if async_init tried, it would block.

        result = await asyncio.wait_for(client.async_init(), timeout=2)
        assert result is True
        assert client.state == "CONNECTED"


class TestAsyncInitUseToken:
    async def test_existing_valid_token_is_used(self, client, fake_ws, monkeypatch):
        client._token = LxToken("existing-token", 10**9, "SHA256")
        client._receive_updates = True
        client._encryption_handler.generate_session_key = AsyncMock(
            return_value="SESSIONKEY"
        )
        _wire_session(monkeypatch, fake_ws)

        _keyexchange_ok(fake_ws)
        # hash_token -> getkey
        fake_ws.queue_header_and_payload(0, ll_response(value=KNOWN_HMAC_KEY_HEX))
        # authwithtoken response (no token field -> keep existing)
        fake_ws.queue_header_and_payload(0, ll_response(value={"validUntil": 2 * 10**9}))
        fake_ws.queue_header_and_payload(0, ll_response(code="200"))  # enable-updates

        result = await asyncio.wait_for(client.async_init(), timeout=2)

        assert result is True
        assert client._token.token == "existing-token"  # reused, not replaced
        assert client._token.valid_until == 2 * 10**9

    async def test_use_token_failure_falls_back_to_acquire(
        self, client, fake_ws, monkeypatch
    ):
        # A valid stored token takes the use_token() path; if that raises (e.g.
        # the Miniserver rejects it), async_init must recover by acquiring a
        # fresh token instead of aborting the handshake.
        client._token = LxToken("existing-token", 10**9, "SHA256")
        client._receive_updates = False
        client._encryption_handler.generate_session_key = AsyncMock(
            return_value="SESSIONKEY"
        )
        _wire_session(monkeypatch, fake_ws)
        _keyexchange_ok(fake_ws)

        monkeypatch.setattr(
            client, "use_token", AsyncMock(side_effect=RuntimeError("token rejected"))
        )
        acquire = AsyncMock()
        monkeypatch.setattr(client, "acquire_token", acquire)

        result = await asyncio.wait_for(client.async_init(), timeout=2)

        assert result is True
        assert client.state == "CONNECTED"
        acquire.assert_awaited_once()


class TestAsyncInitFailure:
    async def test_keyexchange_failure_closes_resources(
        self, client, fake_ws, monkeypatch
    ):
        client._token = LxToken()
        client._encryption_handler.generate_session_key = AsyncMock(
            return_value="SESSIONKEY"
        )
        _wire_session(monkeypatch, fake_ws)

        # Key exchange rejected -> ConnectionError -> cleanup.
        fake_ws.queue_header_and_payload(0, ll_response(code="401"))

        with pytest.raises(ConnectionError):
            await asyncio.wait_for(client.async_init(), timeout=2)

        assert client._ws is None
        assert client._session is None
