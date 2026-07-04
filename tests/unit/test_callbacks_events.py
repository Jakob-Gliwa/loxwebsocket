"""Message- and event-callback tests (T23-T25 + registration/filtering)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from loxwebsocket.lox_ws_api import LoxWs

pytestmark = pytest.mark.asyncio


async def _drain(iterations: int = 5) -> None:
    """Yield to the loop repeatedly so scheduled tasks *and* their
    done-callbacks (which log callback errors) get a chance to run."""
    for _ in range(iterations):
        await asyncio.sleep(0)


class TestMessageCallbacks:
    async def test_register_for_specific_types(self, client):
        cb = AsyncMock()
        client.add_message_callback(cb, message_types=[2])
        assert cb in client._message_callbacks[2]
        assert cb not in client._message_callbacks[0]

    async def test_default_registers_for_all_types(self, client):
        cb = AsyncMock()
        client.add_message_callback(cb)
        for message_type in range(8):
            assert cb in client._message_callbacks[message_type]

    async def test_remove_callback(self, client):
        cb = AsyncMock()
        client.add_message_callback(cb, message_types=[2, 3])
        client.remove_message_callback(cb, message_types=[2, 3])
        assert cb not in client._message_callbacks[2]
        assert cb not in client._message_callbacks[3]


class TestEventDispatch:
    async def test_specific_event_only_fires_matching_subscriber(self, client):
        on_connected = AsyncMock()
        on_closed = AsyncMock()
        client.add_event_callback(on_connected, [LoxWs.EventType.CONNECTED])
        client.add_event_callback(on_closed, [LoxWs.EventType.CONNECTION_CLOSED])

        await client.send_event(LoxWs.EventType.CONNECTED)
        await _drain()  # let scheduled tasks run

        on_connected.assert_awaited_once()
        on_closed.assert_not_awaited()

    # T25 - EventType.ANY receives every event type.
    @pytest.mark.parametrize(
        "event",
        [
            LoxWs.EventType.CONNECTED,
            LoxWs.EventType.RECONNECTED,
            LoxWs.EventType.CONNECTION_CLOSED,
            LoxWs.EventType.INITIALIZED,
        ],
    )
    async def test_any_subscriber_receives_all(self, client, event):
        cb = AsyncMock()
        client.add_event_callback(cb, [LoxWs.EventType.ANY])
        await client.send_event(event)
        await _drain()
        cb.assert_awaited_once()

    async def test_default_event_types_is_any(self, client):
        cb = AsyncMock()
        client.add_event_callback(cb)
        await client.send_event(LoxWs.EventType.RECONNECTED)
        await _drain()
        cb.assert_awaited_once()

    # T24 - dispatch iterates over a snapshot; a subscriber may unsubscribe
    # itself during dispatch without raising "dict changed size".
    async def test_self_unsubscribe_during_dispatch(self, client):
        async def unsubscribe_self():
            client._event_callbacks.pop(unsubscribe_self, None)

        client.add_event_callback(unsubscribe_self, [LoxWs.EventType.ANY])
        # Must not raise despite the callback mutating the registry.
        await client.send_event(LoxWs.EventType.CONNECTED)
        await _drain()
        assert unsubscribe_self not in client._event_callbacks

    async def test_failing_callback_is_isolated(self, client, caplog):
        boom = AsyncMock(side_effect=RuntimeError("boom"))
        healthy = AsyncMock()
        client.add_event_callback(boom, [LoxWs.EventType.ANY])
        client.add_event_callback(healthy, [LoxWs.EventType.ANY])

        await client.send_event(LoxWs.EventType.CONNECTED)
        await _drain()

        # The healthy callback still ran; the failure was logged, not raised.
        healthy.assert_awaited_once()
        assert "Error in event callback" in caplog.text
