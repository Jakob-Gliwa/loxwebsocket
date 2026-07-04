"""Connection lifecycle tests: connect/stop/close-resources + background tasks.

Covers T11 (idempotent resource close + future resolution), T12 (stop return
codes), T13 (URL normalisation), T23 (CONNECTED on connect) plus keep_alive,
ws_listen and start.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from loxwebsocket.lox_ws_api import LoxWs

pytestmark = pytest.mark.asyncio


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# T13 - connect() normalises the URL / derives the ws scheme                  #
# --------------------------------------------------------------------------- #
class TestConnectUrlNormalization:
    @pytest.mark.parametrize(
        ("given", "expected_http", "expected_ws"),
        [
            ("192.168.1.10", "http://192.168.1.10", "ws://192.168.1.10"),
            ("http://ms.local", "http://ms.local", "ws://ms.local"),
            ("https://ms.local", "https://ms.local", "wss://ms.local"),
            # Only the scheme prefix must be rewritten - a host that itself
            # contains "http"/"https" must survive intact.
            ("http://httpbin.org", "http://httpbin.org", "ws://httpbin.org"),
            ("httpbin.org", "http://httpbin.org", "ws://httpbin.org"),
            (
                "https://myhttpshost.example",
                "https://myhttpshost.example",
                "wss://myhttpshost.example",
            ),
        ],
    )
    async def test_url_normalization(
        self, client, monkeypatch, given, expected_http, expected_ws
    ):
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())
        client._ws = None
        client.state = "CLOSED"

        await client.connect("u", "p", given)

        assert client._loxone_url == expected_http
        assert client._loxone_ws_url == expected_ws

    # T23 - connect emits CONNECTED.
    async def test_connect_emits_connected_event(self, client, monkeypatch):
        monkeypatch.setattr(client, "async_init", AsyncMock(return_value=True))
        monkeypatch.setattr(client, "start", AsyncMock())
        send_event = AsyncMock()
        monkeypatch.setattr(client, "send_event", send_event)
        client._ws = None
        client.state = "CLOSED"

        await client.connect("u", "p", "192.168.1.10")

        send_event.assert_awaited_once_with(LoxWs.EventType.CONNECTED)

    async def test_connect_is_noop_when_already_connected(
        self, client, monkeypatch, fake_ws
    ):
        client._ws = fake_ws  # not closed
        client.state = "CONNECTED"
        async_init = AsyncMock()
        monkeypatch.setattr(client, "async_init", async_init)
        monkeypatch.setattr(client, "start", AsyncMock())
        monkeypatch.setattr(client, "send_event", AsyncMock())

        await client.connect("u", "p", "host")

        async_init.assert_not_awaited()


# --------------------------------------------------------------------------- #
# T12 - stop() returns 0 on success, -1 on teardown failure                   #
# --------------------------------------------------------------------------- #
class TestStop:
    async def test_success_returns_zero(self, client):
        client._ws = None
        client._session = None
        assert await client.stop() == 0
        assert client.state == "CLOSED"

    async def test_failure_returns_minus_one(self, client, monkeypatch):
        monkeypatch.setattr(
            client,
            "_close_connection_resources",
            AsyncMock(side_effect=RuntimeError("teardown boom")),
        )
        assert await client.stop() == -1


# --------------------------------------------------------------------------- #
# T11 - _close_connection_resources is idempotent and resolves pending futures#
# --------------------------------------------------------------------------- #
class TestCloseConnectionResources:
    async def test_closes_and_clears_references(self, client, fake_ws):
        session = _FakeSession()
        client._ws = fake_ws
        client._session = session
        client._listener_running = True

        await client._close_connection_resources()

        assert client._ws is None
        assert client._session is None
        assert client._listener_running is False
        assert fake_ws.close_calls == 1
        assert session.closed is True

    async def test_idempotent_second_call_does_not_raise(self, client, fake_ws):
        client._ws = fake_ws
        client._session = _FakeSession()
        await client._close_connection_resources()
        # Second call on already-cleared state must be a safe no-op.
        await client._close_connection_resources()

    async def test_pending_command_future_is_failed(self, client):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client._response_future = future
        client._ws = None
        client._session = None

        await client._close_connection_resources()

        assert future.done()
        with pytest.raises(ConnectionError):
            future.result()


# --------------------------------------------------------------------------- #
# keep_alive                                                                  #
# --------------------------------------------------------------------------- #
class TestKeepAlive:
    async def test_sends_keepalive_until_state_changes(
        self, client, fake_ws, local_sleep_patch, monkeypatch
    ):
        client._ws = fake_ws
        client.state = "CONNECTED"
        sent: list[str] = []

        async def send_and_stop(data):
            sent.append(data)
            client.state = "CLOSED"  # end the loop after one iteration

        monkeypatch.setattr(fake_ws, "send_str", send_and_stop)

        await client.keep_alive(60)

        assert sent == ["keepalive"]

    async def test_exception_triggers_connection_interrupt(
        self, client, fake_ws, local_sleep_patch, monkeypatch
    ):
        client._ws = fake_ws
        client.state = "CONNECTED"
        interrupt = AsyncMock()
        monkeypatch.setattr(client, "handle_connection_interrupt", interrupt)

        async def boom(data):
            raise ConnectionError("socket dead")

        monkeypatch.setattr(fake_ws, "send_str", boom)

        await client.keep_alive(60)

        interrupt.assert_awaited_once()
        assert isinstance(
            interrupt.await_args.kwargs.get("exception"), ConnectionError
        )

    async def test_torn_down_during_sleep_breaks_without_reconnect(
        self, client, fake_ws, monkeypatch
    ):
        """A connection dropped *while keep_alive slept* must exit cleanly.

        Regression guard: without the post-sleep re-check the loop would call
        ``send_str`` on the now-``None`` socket, raise, and be misreported as a
        fresh disconnect - triggering a redundant reconnect() and inflating
        disconnect counts. It must break out silently instead.
        """
        client._ws = fake_ws
        client.state = "CONNECTED"
        interrupt = AsyncMock()
        monkeypatch.setattr(client, "handle_connection_interrupt", interrupt)

        async def teardown_during_sleep(delay, result=None):
            # Simulate reconnect/stop tearing the socket down mid-sleep.
            client.state = "RECONNECTING"
            client._ws = None

        monkeypatch.setattr(
            "loxwebsocket.lox_ws_api.asyncio.sleep", teardown_during_sleep
        )

        await client.keep_alive(60)

        interrupt.assert_not_awaited()
        assert fake_ws.sent == []


# --------------------------------------------------------------------------- #
# ws_listen                                                                   #
# --------------------------------------------------------------------------- #
class TestWsListen:
    async def test_sets_listener_flag_and_dispatches(
        self, client, fake_ws, monkeypatch
    ):
        client._ws = fake_ws
        process = AsyncMock()
        interrupt = AsyncMock()
        monkeypatch.setattr(client, "_async_process_message", process)
        monkeypatch.setattr(client, "handle_connection_interrupt", interrupt)

        fake_ws.queue_text("a").queue_text("b").queue_close(1000)
        await client.ws_listen()

        # ws_listen takes ownership of receive() by arming this flag.
        assert client._listener_running is True
        assert process.await_count == 2
        interrupt.assert_awaited_once()
        # The close frame is never yielded by aiohttp's async iterator, so the
        # loop must not forward a stale `msg.type` from the last data message.
        # The handler reads self._ws.close_code instead -> called with no args.
        assert interrupt.await_args.args == ()
        assert "msg_type" not in interrupt.await_args.kwargs

    async def test_inner_exception_continues_loop(
        self, client, fake_ws, monkeypatch, caplog
    ):
        client._ws = fake_ws
        seen: list[str] = []

        async def process(data):
            seen.append(data)
            if data == "bad":
                raise ValueError("boom")

        monkeypatch.setattr(client, "_async_process_message", process)
        monkeypatch.setattr(client, "handle_connection_interrupt", AsyncMock())

        fake_ws.queue_text("ok").queue_text("bad").queue_text("ok2").queue_close()
        await client.ws_listen()

        # The loop kept going after the failing message.
        assert seen == ["ok", "bad", "ok2"]


# --------------------------------------------------------------------------- #
# start                                                                       #
# --------------------------------------------------------------------------- #
class TestStart:
    async def test_starts_three_named_tasks(self, client, monkeypatch):
        monkeypatch.setattr(client, "ws_listen", AsyncMock())
        monkeypatch.setattr(client, "keep_alive", AsyncMock())
        monkeypatch.setattr(client, "refresh_token", AsyncMock())

        await client.start()
        try:
            names = {t.get_name() for t in client.background_tasks}
            assert {"consumer_task", "keepalive", "refresh_token"} <= names
        finally:
            for task in list(client.background_tasks):
                task.cancel()

    async def test_cancels_previous_tasks(self, client, monkeypatch):
        async def long_running():
            await asyncio.sleep(100)

        old = asyncio.create_task(long_running())
        client.background_tasks.add(old)

        monkeypatch.setattr(client, "ws_listen", AsyncMock())
        monkeypatch.setattr(client, "keep_alive", AsyncMock())
        monkeypatch.setattr(client, "refresh_token", AsyncMock())

        await client.start()
        await asyncio.sleep(0)  # let the cancellation propagate

        assert old.cancelled()
        for task in list(client.background_tasks):
            task.cancel()
