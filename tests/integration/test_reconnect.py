"""Reconnect, resource-leak, close-code and http_ping tests.

Covers T9 (token reset via ``self._token``), T10 (each failed attempt closes
its ClientSession), T14 (unlimited log), T23 (CONNECTION_CLOSED/RECONNECTED
events), T26 (close-code classification) and T27 (http_ping uses the logger).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from support.ws_fake import FakeWSTransport

from loxwebsocket.exceptions import LoxoneException
from loxwebsocket.lox_ws_api import LoxWs
from loxwebsocket.lxtoken import LxToken

pytestmark = pytest.mark.asyncio


class _SpySession:
    """aiohttp.ClientSession stand-in whose ws_connect always fails."""

    def __init__(self, registry):
        self.close_calls = 0
        registry.append(self)

    async def ws_connect(self, *args, **kwargs):
        raise ConnectionError("cannot connect")

    async def close(self):
        self.close_calls += 1


# --------------------------------------------------------------------------- #
# T9 - reconnect resets self._token (not a stray self.token)                  #
# --------------------------------------------------------------------------- #
class TestReconnectTokenReset:
    async def test_token_reset_via_private_attr(
        self, client, local_sleep_patch, monkeypatch
    ):
        client.state = "CONNECTED"
        client._max_reconnect_attempts = 1
        client._token = LxToken("old-token", 999_999_999)

        monkeypatch.setattr(client, "stop", AsyncMock())
        monkeypatch.setattr(client, "http_ping", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())

        await client.reconnect()

        assert isinstance(client._token, LxToken)
        assert client._token.token == ""
        # The historic bug wrote to a dead ``self.token`` attribute.
        assert not hasattr(client, "token")

    async def test_reconnected_event_emitted(
        self, client, local_sleep_patch, monkeypatch
    ):
        client.state = "CONNECTED"
        client._max_reconnect_attempts = 1
        monkeypatch.setattr(client, "stop", AsyncMock())
        monkeypatch.setattr(client, "http_ping", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        send_event = AsyncMock()
        monkeypatch.setattr(client, "send_event", send_event)

        await client.reconnect()

        send_event.assert_awaited_once_with(LoxWs.EventType.RECONNECTED)


# --------------------------------------------------------------------------- #
# reconnect() is re-entrant-safe: a call while already RECONNECTING is a no-op #
# so two overlapping interrupts don't spawn competing reconnect loops.        #
# --------------------------------------------------------------------------- #
class TestReconnectReentrancy:
    async def test_noop_when_already_reconnecting(self, client, monkeypatch):
        client.state = "RECONNECTING"
        stop = AsyncMock()
        monkeypatch.setattr(client, "stop", stop)
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))

        await client.reconnect()

        # Guard hit before any teardown/attempt work.
        stop.assert_not_awaited()
        client.async_init.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Stale background tasks are cancelled at teardown, not deferred to start()    #
# --------------------------------------------------------------------------- #
class TestCancelStaleBackgroundTasks:
    async def test_cancels_tasks_when_called_outside_background_tasks(self, client):
        async def long_running():
            await asyncio.Event().wait()  # blocks until cancelled

        t1 = asyncio.create_task(long_running())
        t2 = asyncio.create_task(long_running())
        client.background_tasks.update({t1, t2})
        await asyncio.sleep(0)  # let both start and block

        client._cancel_stale_background_tasks()
        await asyncio.sleep(0)  # let cancellation propagate

        assert t1.cancelled()
        assert t2.cancelled()
        assert client.background_tasks == set()

    async def test_spares_the_current_driving_task(self, client):
        """The task calling the helper (the reconnect driver) must survive.

        Cancelling it would raise CancelledError at the next await and abort
        the whole reconnect loop - the exact regression this exclusion guards
        against. The stale sibling must still be cancelled.
        """
        stale = asyncio.create_task(asyncio.Event().wait())
        client.background_tasks.add(stale)
        await asyncio.sleep(0)  # let the stale task block

        result: dict[str, object] = {}

        async def driver():
            client.background_tasks.add(asyncio.current_task())
            client._cancel_stale_background_tasks()
            # If the helper had cancelled us, this await would raise.
            await asyncio.sleep(0)
            result["driver_survived"] = True
            result["stale_cancelled"] = stale.cancelled()
            result["current_still_in_set"] = (
                asyncio.current_task() in client.background_tasks
            )

        driver_task = asyncio.create_task(driver())
        await driver_task

        assert result["driver_survived"] is True
        assert result["stale_cancelled"] is True
        # Left in the set so start() cancels it later, exactly as before.
        assert result["current_still_in_set"] is True

    async def test_reconnect_cancels_stale_task_before_first_attempt(
        self, client, local_sleep_patch, monkeypatch
    ):
        """End-to-end: a stale task present at reconnect start ends up cancelled.

        Uses a real stop() so the teardown path runs. The stale task blocks on
        an Event (not asyncio.sleep) so local_sleep_patch - which speeds up
        reconnect's own delay - can't resolve it early and mask the cancel.
        """
        client.state = "CONNECTED"
        client._max_reconnect_attempts = 1
        client._ws = None
        client._session = None
        monkeypatch.setattr(client, "http_ping", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())

        stale = asyncio.create_task(asyncio.Event().wait())
        client.background_tasks.add(stale)
        await asyncio.sleep(0)  # let it start and block

        await client.reconnect()
        await asyncio.sleep(0)  # let cancellation propagate

        assert stale.cancelled()
        assert stale not in client.background_tasks


# --------------------------------------------------------------------------- #
# T10 - every failed attempt closes its ClientSession                         #
# --------------------------------------------------------------------------- #
class TestSessionLeak:
    async def test_async_init_failure_closes_session(self, client, monkeypatch):
        sessions: list[_SpySession] = []
        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.aiohttp.ClientSession",
            lambda *a, **k: _SpySession(sessions),
        )
        client._encryption_handler.generate_session_key = AsyncMock(return_value="k")

        with pytest.raises(ConnectionError):
            await client.async_init()

        assert len(sessions) == 1
        assert sessions[0].close_calls == 1
        assert client._session is None
        assert client._ws is None

    async def test_reconnect_closes_session_each_attempt(
        self, client, local_sleep_patch, monkeypatch
    ):
        sessions: list[_SpySession] = []
        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.aiohttp.ClientSession",
            lambda *a, **k: _SpySession(sessions),
        )
        client._encryption_handler.generate_session_key = AsyncMock(return_value="k")
        monkeypatch.setattr(client, "http_ping", AsyncMock(return_value=True))
        client.state = "CONNECTED"
        client._max_reconnect_attempts = 3

        with pytest.raises(LoxoneException):
            await client.reconnect()

        assert len(sessions) == 3
        assert all(s.close_calls == 1 for s in sessions)
        assert client._session is None


# --------------------------------------------------------------------------- #
# T14 - reconnect log shows "unlimited" for max_reconnect_attempts == 0       #
# --------------------------------------------------------------------------- #
class TestReconnectLog:
    async def test_unlimited_label(
        self, client, local_sleep_patch, monkeypatch, caplog
    ):
        client.state = "CONNECTED"
        client._max_reconnect_attempts = 0
        monkeypatch.setattr(client, "stop", AsyncMock())
        monkeypatch.setattr(client, "http_ping", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())

        with caplog.at_level(logging.INFO, logger="loxwebsocket.lox_ws_api"):
            await client.reconnect()

        assert "Reconnect attempt 1 of unlimited" in caplog.text


# --------------------------------------------------------------------------- #
# T26 - close-code classification                                             #
# --------------------------------------------------------------------------- #
_CODE_CASES = [
    (1000, "INFO", "normally"),
    (1001, "INFO", "going away"),
    (1005, "WARNING", "without a status code"),
    (1006, "WARNING", "abnormally"),
    (1011, "ERROR", "internal error"),
    (1012, "INFO", "restarting"),
    (4003, "ERROR", "too many failed login"),
    (4004, "ERROR", "Some user has been changed"),
    (4005, "ERROR", "currently connected has been changed"),
    (4006, "ERROR", "has been disabled"),
    (4007, "ERROR", "performing an update"),
    (4008, "ERROR", "event slots"),
    (None, "ERROR", "without a close code"),
    (4999, "ERROR", "unrecognized code"),
]


class TestCloseCodeClassification:
    @pytest.mark.parametrize(("code", "level", "substr"), _CODE_CASES)
    async def test_classification(
        self, client, monkeypatch, caplog, code, level, substr
    ):
        monkeypatch.setattr(client, "reconnect", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())
        client._ws = FakeWSTransport(close_code=code)
        client.state = "CONNECTED"

        with caplog.at_level(logging.DEBUG, logger="loxwebsocket.lox_ws_api"):
            await client.handle_connection_interrupt()

        record = next(r for r in caplog.records if substr in r.getMessage())
        assert record.levelname == level
        client.reconnect.assert_awaited_once()

    async def test_exception_is_logged_with_type(self, client, monkeypatch, caplog):
        # When called with an exception (e.g. from keep_alive / ws_listen), the
        # error and its type are logged in addition to the close-code line.
        monkeypatch.setattr(client, "reconnect", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())
        client._ws = FakeWSTransport(close_code=1006)
        client.state = "CONNECTED"

        with caplog.at_level(logging.ERROR, logger="loxwebsocket.lox_ws_api"):
            await client.handle_connection_interrupt(
                exception=ConnectionError("socket dead")
            )

        record = next(
            r for r in caplog.records if "Connection error" in r.getMessage()
        )
        assert "socket dead" in record.getMessage()
        assert "ConnectionError" in record.getMessage()

    async def test_connection_closed_event_emitted(self, client, monkeypatch):
        monkeypatch.setattr(client, "reconnect", AsyncMock())
        send_event = AsyncMock()
        monkeypatch.setattr(client, "send_event", send_event)
        client._ws = FakeWSTransport(close_code=1006)
        client.state = "CONNECTED"

        await client.handle_connection_interrupt()

        send_event.assert_awaited_once_with(LoxWs.EventType.CONNECTION_CLOSED)

    async def test_no_event_while_already_reconnecting(self, client, monkeypatch):
        monkeypatch.setattr(client, "reconnect", AsyncMock())
        send_event = AsyncMock()
        monkeypatch.setattr(client, "send_event", send_event)
        client._ws = FakeWSTransport(close_code=1006)
        client.state = "RECONNECTING"

        await client.handle_connection_interrupt()

        send_event.assert_not_awaited()


# --------------------------------------------------------------------------- #
# T27 - http_ping uses the logger (no print) and returns a bool               #
# --------------------------------------------------------------------------- #
class _PingResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _PingSession:
    def __init__(self, status=200, fail=False):
        self._status = status
        self._fail = fail

    async def __aenter__(self):
        if self._fail:
            raise ConnectionError("host down")
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, *args, **kwargs):
        return _PingResponse(self._status)


class TestHttpPing:
    async def test_success_returns_true(self, client, monkeypatch):
        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.aiohttp.ClientSession",
            lambda *a, **k: _PingSession(status=200),
        )
        assert await client.http_ping() is True

    async def test_non_200_returns_false(self, client, monkeypatch):
        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.aiohttp.ClientSession",
            lambda *a, **k: _PingSession(status=503),
        )
        assert await client.http_ping() is False

    async def test_failure_logs_debug_without_print(
        self, client, monkeypatch, caplog, capsys
    ):
        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.aiohttp.ClientSession",
            lambda *a, **k: _PingSession(fail=True),
        )
        with caplog.at_level(logging.DEBUG, logger="loxwebsocket.lox_ws_api"):
            result = await client.http_ping()

        assert result is False
        assert "HTTP reachability check failed" in caplog.text
        assert capsys.readouterr().out == ""
