"""Public API contract tests (T28 + user-facing surface from the README)."""

from __future__ import annotations

import importlib
import importlib.metadata
from unittest.mock import AsyncMock

import pytest

import loxwebsocket
from loxwebsocket import (
    LoxoneException,
    LoxoneHTTPStatusError,
    LoxoneRequestError,
    LoxWs,
    LxToken,
)


# --------------------------------------------------------------------------- #
# T28 - exports are real and importable                                       #
# --------------------------------------------------------------------------- #
class TestExports:
    def test_documented_exceptions_import(self):
        assert issubclass(LoxoneHTTPStatusError, LoxoneException)
        assert issubclass(LoxoneRequestError, Exception)

    def test_all_symbols_are_importable(self):
        module = importlib.import_module("loxwebsocket")
        for name in module.__all__:
            assert hasattr(module, name), f"__all__ lists missing symbol {name!r}"

    def test_expected_public_names(self):
        assert set(loxwebsocket.__all__) == {
            "LoxWs",
            "LoxoneException",
            "LoxoneHTTPStatusError",
            "LoxoneRequestError",
            "LxToken",
        }

    def test_event_type_enum_members(self):
        names = {e.name for e in LoxWs.EventType}
        assert names == {
            "ANY",
            "INITIALIZED",
            "CONNECTED",
            "CONNECTION_CLOSED",
            "RECONNECTED",
        }

    def test_version_matches_package_metadata(self):
        # __version__ is derived from the installed package metadata
        # (pyproject.toml is the single source of truth), so the two must agree.
        assert loxwebsocket.__version__ == importlib.metadata.version("loxwebsocket")


# --------------------------------------------------------------------------- #
# connect(...) orchestration (README quickstart smoke)                        #
# --------------------------------------------------------------------------- #
class TestConnectContract:
    async def _patch_internals(self, client, monkeypatch):
        async_init = AsyncMock(return_value=True)
        start = AsyncMock()
        send_event = AsyncMock()
        monkeypatch.setattr(client, "async_init", async_init)
        monkeypatch.setattr(client, "start", start)
        monkeypatch.setattr(client, "send_event", send_event)
        return async_init, start, send_event

    @pytest.mark.asyncio
    async def test_quickstart_flow(self, client, monkeypatch):
        async_init, start, send_event = await self._patch_internals(client, monkeypatch)
        client._ws = None
        client.state = "CLOSED"

        await client.connect(
            user="your-username",
            password="your-password",
            loxone_url="http://miniserver-ip-or-host",
            receive_updates=True,
            max_reconnect_attempts=5,
        )

        async_init.assert_awaited_once()
        start.assert_awaited_once()
        send_event.assert_awaited_once_with(LoxWs.EventType.CONNECTED)
        assert client._receive_updates is True
        assert client._max_reconnect_attempts == 5
        assert client._username == "your-username"


# --------------------------------------------------------------------------- #
# Callbacks - exact README usage                                              #
# --------------------------------------------------------------------------- #
class TestCallbackContract:
    def test_message_callback_readme_usage(self, client):
        async def on_value_update(data, message_type):
            ...

        client.add_message_callback(on_value_update, message_types=[2])
        assert on_value_update in client._message_callbacks[2]

    def test_event_callback_readme_usage(self, client):
        async def on_connected():
            ...

        client.add_event_callback(
            on_connected, event_types=[LoxWs.EventType.CONNECTED]
        )
        assert client._event_callbacks[on_connected] == [LoxWs.EventType.CONNECTED]


# --------------------------------------------------------------------------- #
# LxToken public surface                                                      #
# --------------------------------------------------------------------------- #
class TestLxTokenContract:
    def test_properties_and_setters(self):
        token = LxToken()
        token.set_token("t")
        token.set_valid_until(10)
        token.set_hash_alg("SHA256")
        assert token.token == "t"
        assert token.valid_until == 10
        assert token.hash_alg == "SHA256"
        assert isinstance(token.get_seconds_to_expire(), int)


# --------------------------------------------------------------------------- #
# Initialisation contract                                                     #
# --------------------------------------------------------------------------- #
class TestInitialisationContract:
    def test_each_construction_is_independent(self):
        # Despite the "singleton" wording in the source, LoxWs has no __new__:
        # every construction is a distinct, fully-initialised object with its
        # own mutable state (only the callback registry is class-level).
        a = LoxWs()
        b = LoxWs()
        assert a is not b
        assert a._message_callbacks is not b._message_callbacks

    def test_reinit_of_same_instance_preserves_state(self):
        # The class-level _initialized guard exists to stop a second __init__
        # on the *same* instance from wiping live state.
        a = LoxWs()
        a.state = "CONNECTED"
        a._token = LxToken("keep-me", 123)
        a.__init__()
        assert a.state == "CONNECTED"
        assert a._token.token == "keep-me"
