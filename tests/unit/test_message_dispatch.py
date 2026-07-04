"""Message dispatch tests: _async_process_message + extract_type_* handlers."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from support.crypto_vectors import build_encrypted_control_response
from support.loxone_builders import (
    build_header,
    build_text_states,
    build_value_states,
    expected_text_dict,
    expected_value_dict,
    ll_response,
)

pytestmark = pytest.mark.asyncio


async def _drain(iterations: int = 5) -> None:
    for _ in range(iterations):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# _async_process_message                                                      #
# --------------------------------------------------------------------------- #
class TestAsyncProcessMessage:
    async def test_header_sets_type_and_returns(self, client):
        await client._async_process_message(build_header(2, 24))
        assert client._current_message_type == 2

    async def test_type2_dispatches_to_callback(self, client):
        cb = AsyncMock()
        client.add_message_callback(cb, [2])
        raw = bytes(range(16))
        payload = build_value_states([(raw, 3.5)])

        await client._async_process_message(build_header(2, len(payload)))
        await client._async_process_message(payload)
        await _drain()

        cb.assert_awaited_once()
        data, msg_type = cb.await_args.args
        assert msg_type == 2
        assert data == expected_value_dict([(raw, 3.5)])
        # Type is reset after dispatch.
        assert client._current_message_type is None

    async def test_callback_filtering_by_type(self, client):
        cb_for_3 = AsyncMock()
        client.add_message_callback(cb_for_3, [3])
        payload = build_value_states([(bytes(range(16)), 1.0)])

        await client._async_process_message(build_header(2, len(payload)))
        await client._async_process_message(payload)
        await _drain()

        cb_for_3.assert_not_awaited()

    async def test_type0_with_pending_future_resolves_and_skips_handler(self, client):
        loop = asyncio.get_running_loop()
        client._response_future = loop.create_future()
        cb = AsyncMock()
        client.add_message_callback(cb, [0])
        payload = b'{"LL":{"Code":"200"}}'

        await client._async_process_message(build_header(0, len(payload)))
        await client._async_process_message(payload)
        await _drain()

        assert client._response_future.result() == payload
        assert client._current_message_type is None
        # Handler/callbacks are bypassed when the future consumes the message.
        cb.assert_not_awaited()


# --------------------------------------------------------------------------- #
# extract_type_0_message branches                                             #
# --------------------------------------------------------------------------- #
class TestExtractType0:
    async def test_missing_ll_returns_none(self, client):
        assert await client.extract_type_0_message(json.dumps({"x": 1}), {}) is None

    async def test_non_200_returns_none(self, client):
        assert await client.extract_type_0_message(ll_response(code="401"), {}) is None

    async def test_404_with_control_warns_and_returns_none(self, client, caplog):
        msg = json.dumps({"LL": {"Code": "404", "control": "jdev/unknown"}})
        with caplog.at_level(logging.WARNING, logger="loxwebsocket.lox_ws_api"):
            assert await client.extract_type_0_message(msg, {}) is None
        assert "Unrecognized command or control not found" in caplog.text
        assert "jdev/unknown" in caplog.text

    async def test_plain_json_value_is_returned(self, client):
        msg = ll_response(code="200", value="42")
        result = await client.extract_type_0_message(msg, {})
        assert result["LL"]["value"] == "42"

    async def test_control_response_is_decrypted_and_keyed(self, client):
        plaintext = "jdev/sps/io/0f1e2d3c/1"
        control_cipher = build_encrypted_control_response(plaintext)
        msg = json.dumps({"LL": {"Code": "200", "control": control_cipher}})

        event_dict = {}
        result = await client.extract_type_0_message(msg, event_dict)

        assert b"0f1e2d3c" in result
        assert result[b"0f1e2d3c"]["control"] == plaintext

    async def test_invalid_json_falls_back_to_decoded_string(self, client):
        assert await client.extract_type_0_message(b"not json", {}) == "not json"

    async def test_key_salt_secure_branch(self, client):
        from support.crypto_vectors import KNOWN_HMAC_KEY_HEX

        msg = json.dumps(
            {
                "LL": {
                    "Code": "200",
                    "value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "s", "hashAlg": "SHA1"},
                }
            }
        )
        assert await client.extract_type_0_message(msg, {}) is None
        assert client._visual_hash is not None


# --------------------------------------------------------------------------- #
# extract_type_1 / 2 / 3 / 6 / other                                          #
# --------------------------------------------------------------------------- #
class TestOtherExtractors:
    async def test_type1_returns_event_dict(self, client):
        assert await client.extract_type_1_message(b"binary-file", {}) == {}

    async def test_type2_delegates_to_parser(self, client):
        raw = bytes(range(16))
        payload = build_value_states([(raw, 9.0)])
        assert await client.extract_type_2_message(payload, {}) == expected_value_dict(
            [(raw, 9.0)]
        )

    async def test_type3_delegates_to_parser(self, client):
        raw = bytes([1] * 16)
        icon = bytes([2] * 16)
        payload = build_text_states([(raw, icon, b"Hallo")])
        assert await client.extract_type_3_message(payload, {}) == expected_text_dict(
            [(raw, icon, b"Hallo")]
        )

    async def test_type6_keepalive(self, client):
        event_dict = {}
        result = await client.extract_type_6_message(b"", event_dict)
        assert result["keep_alive"] == "received"

    async def test_other_messages_set_type_7(self, client):
        client._current_message_type = 4
        result = await client.extract_other_messages(b"", {})
        assert client._current_message_type == 7
        assert result == {}
