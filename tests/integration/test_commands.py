"""Command send/response tests (T20 + send_websocket_command + visu-secured)."""

from __future__ import annotations

import asyncio

import pytest
from support.crypto_vectors import KNOWN_HMAC_KEY_HEX, decrypt_command
from support.loxone_builders import build_header

import loxwebsocket.const as c

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# T20 - send_command routing vs. the listener                                 #
# --------------------------------------------------------------------------- #
class TestSendCommandRouting:
    async def test_direct_path_without_listener(self, client, fake_ws):
        """No listener -> send_command reads the response frames itself."""
        client._ws = fake_ws
        client._listener_running = False
        payload = b'{"LL":{"Code":"200","value":"1"}}'
        fake_ws.queue_header_and_payload(0, payload)

        result = await client.send_command("jdev/sps/io/AI1/state")

        assert result == payload
        assert len(fake_ws.sent) == 1
        assert fake_ws.sent[0].startswith(c.CMD_ENCRYPT_CMD)

    async def test_listener_path_uses_response_future(self, client, fake_ws):
        """With the listener owning the socket, the response arrives via the
        future set by ``_async_process_message`` - receive() is never called."""
        client._ws = fake_ws
        client._listener_running = True
        payload = b'{"LL":{"Code":"200","value":"ok"}}'

        send_task = asyncio.create_task(client.send_command("jdev/cmd"))
        await asyncio.sleep(0)  # let it send and arm the future
        assert client._response_future is not None

        # Simulate the listener delivering a type-0 response (header + payload).
        await client._async_process_message(build_header(0, len(payload)))
        await client._async_process_message(payload)

        result = await send_task
        assert result == payload
        # The command frame was sent, but the socket was never receive()'d.
        assert len(fake_ws.sent) == 1

    async def test_value_update_does_not_resolve_command_future(self, client, fake_ws):
        """Type 2/3/6 frames must not satisfy a pending command future."""
        client._ws = fake_ws
        client._listener_running = True

        send_task = asyncio.create_task(client.send_command("jdev/cmd"))
        await asyncio.sleep(0)
        assert client._response_future is not None

        # A keepalive (type 6) frame arrives - must NOT resolve the future.
        await client._async_process_message(build_header(6, 0))
        await client._async_process_message(b"")
        await asyncio.sleep(0)
        assert not send_task.done()

        # The real type-0 response resolves it.
        payload = b'{"LL":{"Code":"200"}}'
        await client._async_process_message(build_header(0, len(payload)))
        await client._async_process_message(payload)
        assert await send_task == payload

    async def test_listener_path_times_out(self, client, fake_ws, monkeypatch):
        """If the listener never delivers a response the command must not hang
        forever - it times out and clears the pending future."""
        client._ws = fake_ws
        client._listener_running = True
        monkeypatch.setattr(c, "TIMEOUT", 0.01)

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await client.send_command("jdev/cmd")

        # The finally-block cleared the future so the next command starts clean.
        assert client._response_future is None


# --------------------------------------------------------------------------- #
# _receive_command_response - direct (no-listener) read of header + payload   #
# --------------------------------------------------------------------------- #
class TestReceiveCommandResponse:
    async def test_closed_while_waiting_for_header_raises(self, client, fake_ws):
        client._ws = fake_ws
        fake_ws.queue_close()
        with pytest.raises(ConnectionError, match="waiting for header"):
            await client._receive_command_response()

    async def test_closed_while_waiting_for_data_raises(self, client, fake_ws):
        client._ws = fake_ws
        fake_ws.queue_binary(build_header(0, 5))  # valid header first
        fake_ws.queue_close()  # then the socket dies before the payload
        with pytest.raises(ConnectionError, match="waiting for data"):
            await client._receive_command_response()


class TestConcurrentReceiveGuard:
    async def test_fake_ws_rejects_concurrent_receive(self, fake_ws):
        """The double mirrors aiohttp: overlapping receive() raises. This is
        the failure mode the future-based routing exists to avoid."""
        blocked = asyncio.create_task(fake_ws.receive())  # blocks (queue empty)
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="Concurrent"):
            await fake_ws.receive()

        fake_ws.queue_text("release")
        assert (await blocked).data == "release"


# --------------------------------------------------------------------------- #
# send_websocket_command (fire-and-forget control commands)                   #
# --------------------------------------------------------------------------- #
class TestSendWebsocketCommand:
    async def test_string_uuid(self, client, fake_ws):
        client._ws = fake_ws
        await client.send_websocket_command("0f1e2d3c", "on")
        assert decrypt_command(fake_ws.sent[0]).endswith("jdev/sps/io/0f1e2d3c/on")

    async def test_bytes_uuid_is_decoded(self, client, fake_ws):
        client._ws = fake_ws
        await client.send_websocket_command(b"0f1e2d3c", "off")
        assert decrypt_command(fake_ws.sent[0]).endswith("jdev/sps/io/0f1e2d3c/off")


# --------------------------------------------------------------------------- #
# Visualization-password secured commands (queue + salt/key + flush)          #
# --------------------------------------------------------------------------- #
class TestVisuSecuredCommands:
    async def test_queue_then_flush_on_salt_response(self, client, fake_ws):
        client._ws = fake_ws
        client._username = "admin"

        # 1) Queue a secured command -> triggers a getvisusalt request.
        await client.send_websocket_command_to_visu_password_secured_control(
            "dev1", "on", "visu-pw"
        )
        assert len(fake_ws.sent) == 1
        assert decrypt_command(fake_ws.sent[0]).endswith(
            f"{c.CMD_GET_VISUAL_PASSWD}admin"
        )

        # 2) Feed the salt/key response -> the queued command is flushed.
        # Mirrors production: extract_type_0_message passes an already-parsed
        # dict into get_key_salt_for_secure_commands_and_send.
        salt_response = {
            "LL": {
                "value": {
                    "key": KNOWN_HMAC_KEY_HEX,
                    "salt": "abcd",
                    "hashAlg": "SHA256",
                }
            }
        }
        await client.get_key_salt_for_secure_commands_and_send(salt_response)

        assert len(fake_ws.sent) == 2
        # Plaintext is "salt/<salt>/jdev/sps/ios/<visu-hash>/dev1/on".
        flushed = decrypt_command(fake_ws.sent[1])
        assert "jdev/sps/ios/" in flushed
        assert flushed.endswith("/dev1/on")
        # The visu-hash embedded in the command must be the exact HMAC the
        # handler computes for this password/key/salt - not just any hex blob.
        expected_hash = client._encryption_handler.hash_visu_password_secured_command(
            client._visual_hash, "visu-pw"
        )
        assert f"jdev/sps/ios/{expected_hash}/dev1/on" in flushed

    async def test_visual_hash_is_stored(self, client, fake_ws):
        client._ws = fake_ws
        salt_response = {
            "LL": {"value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "s", "hashAlg": "SHA1"}}
        }
        await client.get_key_salt_for_secure_commands_and_send(salt_response)
        assert client._visual_hash is not None
        assert client._visual_hash.hash_alg == "SHA1"
