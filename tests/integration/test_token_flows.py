"""Token acquisition/refresh tests (T15, T16, T17, T19 + hashing flows)."""

from __future__ import annotations

import asyncio
import binascii
import datetime as dt
import json
import logging
from unittest.mock import AsyncMock

import pytest
import time_machine
from Crypto.Hash import HMAC, SHA1, SHA256
from support.crypto_vectors import KNOWN_HMAC_KEY_HEX
from support.loxone_builders import ll_response

import loxwebsocket.const as c
from loxwebsocket.exceptions import LoxoneException
from loxwebsocket.lxtoken import LxToken

pytestmark = pytest.mark.asyncio

_NOW = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# T15 - handleValidUntilMessage adopts new token string and validUntil        #
# --------------------------------------------------------------------------- #
class TestHandleValidUntilMessage:
    async def test_adopts_token_and_valid_until(self, client):
        client._token = LxToken("old", 1)
        msg = ll_response(value={"validUntil": 5000, "token": "newtok"})
        await client.handleValidUntilMessage(msg)
        assert client._token.valid_until == 5000
        assert client._token.token == "newtok"

    async def test_lowercase_code_accepted(self, client):
        client._token = LxToken("old", 1)
        msg = ll_response(value={"validUntil": 6000, "token": "t"}, code_key="code")
        await client.handleValidUntilMessage(msg)
        assert client._token.valid_until == 6000

    async def test_authwithtoken_response_keeps_existing_token(self, client):
        # A plain authwithtoken response has no "token" field -> keep the old one.
        client._token = LxToken("keep-me", 1)
        msg = ll_response(value={"validUntil": 7000})
        await client.handleValidUntilMessage(msg)
        assert client._token.valid_until == 7000
        assert client._token.token == "keep-me"

    async def test_non_200_raises(self, client):
        client._token = LxToken("old", 1)
        msg = ll_response(code="401", value={"validUntil": 0})
        with pytest.raises(LoxoneException):
            await client.handleValidUntilMessage(msg)

    async def test_malformed_response_raises(self, client):
        with pytest.raises(LoxoneException):
            await client.handleValidUntilMessage(json.dumps({"LL": {"Code": "200"}}))


# --------------------------------------------------------------------------- #
# T16 - refresh_token loop survives exceptions and keeps retrying             #
# --------------------------------------------------------------------------- #
class TestRefreshTokenLoop:
    async def test_loop_survives_exceptions(
        self, client, local_sleep_patch, monkeypatch, caplog
    ):
        client._token = LxToken(valid_until=0)
        monkeypatch.setattr(
            client, "_refresh_token", AsyncMock(side_effect=RuntimeError("boom"))
        )

        with caplog.at_level(logging.ERROR, logger="loxwebsocket.lox_ws_api"):
            task = asyncio.create_task(client.refresh_token())
            for _ in range(10):
                await asyncio.sleep(0)

            # Despite every refresh raising, the background loop is still alive
            # and kept retrying (would previously have died on the first error).
            assert not task.done()
            assert client._refresh_token.await_count >= 2

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert "Token refresh failed" in caplog.text


# --------------------------------------------------------------------------- #
# T17 - refresh happens ahead of expiry (with CONNECT_DELAY floor)            #
# --------------------------------------------------------------------------- #
class TestRefreshAhead:
    async def test_sleeps_until_before_expiry(
        self, client, local_sleep_patch, monkeypatch
    ):
        monkeypatch.setattr(
            client, "_refresh_token", AsyncMock(side_effect=asyncio.CancelledError)
        )
        with time_machine.travel(_NOW, tick=False):
            client._token = LxToken(valid_until=1_000_000_000)
            expected = (
                client._token.get_seconds_to_expire()
                - c.TOKEN_REFRESH_SECONDS_BEFORE_EXPIRY
            )
            assert expected > c.CONNECT_DELAY  # sanity: floor not in play here

            task = asyncio.create_task(client.refresh_token())
            with pytest.raises(asyncio.CancelledError):
                await task

        assert local_sleep_patch.calls[0] == expected


# --------------------------------------------------------------------------- #
# T19 - refresh loop never busy-spins (floor at CONNECT_DELAY)                #
# --------------------------------------------------------------------------- #
class TestRefreshBusyLoopGuard:
    async def test_expired_token_sleeps_connect_delay(
        self, client, local_sleep_patch, monkeypatch
    ):
        monkeypatch.setattr(
            client, "_refresh_token", AsyncMock(side_effect=asyncio.CancelledError)
        )
        # valid_until=0 -> already long expired -> negative remainder -> floor.
        client._token = LxToken(valid_until=0)

        task = asyncio.create_task(client.refresh_token())
        with pytest.raises(asyncio.CancelledError):
            await task

        assert local_sleep_patch.calls[0] == c.CONNECT_DELAY


# --------------------------------------------------------------------------- #
# hash_token / use_token / acquire_token                                      #
# --------------------------------------------------------------------------- #
class TestHashToken:
    @pytest.mark.parametrize("alg", ["SHA1", "SHA256"])
    async def test_hash_token_uses_token_algorithm(self, client, monkeypatch, alg):
        client._token = LxToken("mytoken", 0, alg)
        monkeypatch.setattr(
            client, "send_command", AsyncMock(return_value=ll_response(value=KNOWN_HMAC_KEY_HEX))
        )
        digestmod = SHA1 if alg == "SHA1" else SHA256
        expected = HMAC.new(
            binascii.unhexlify(KNOWN_HMAC_KEY_HEX), b"mytoken", digestmod
        ).hexdigest()
        assert await client.hash_token() == expected

    async def test_invalid_key_raises(self, client, monkeypatch):
        client._token = LxToken("mytoken", 0, "SHA1")
        monkeypatch.setattr(
            client, "send_command", AsyncMock(return_value=ll_response(value="nothex!!"))
        )
        with pytest.raises(LoxoneException):
            await client.hash_token()


class TestUseToken:
    async def test_use_token_sends_auth_and_updates_validity(self, client, monkeypatch):
        client._username = "admin"
        monkeypatch.setattr(client, "hash_token", AsyncMock(return_value="deadbeef"))
        send_command = AsyncMock(return_value=ll_response(value={"validUntil": 4242}))
        monkeypatch.setattr(client, "send_command", send_command)

        await client.use_token()

        send_command.assert_awaited_once_with(f"{c.CMD_AUTH_WITH_TOKEN}deadbeef/admin")
        assert client._token.valid_until == 4242


class TestAcquireToken:
    @pytest.mark.parametrize(
        ("version", "expected_prefix"),
        [(15.0, c.CMD_REQUEST_TOKEN_JSON_WEB), (9.0, c.CMD_REQUEST_TOKEN)],
    )
    async def test_acquire_token_version_prefix(
        self, client, monkeypatch, version, expected_prefix
    ):
        client._version = version
        client._username = "admin"
        client._password = "secret"

        getkey2 = json.dumps(
            {"LL": {"value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "abc", "hashAlg": "SHA256"}}}
        )
        token_resp = ll_response(value={"token": "TOK", "validUntil": 5555})
        send_command = AsyncMock(side_effect=[getkey2, token_resp])
        monkeypatch.setattr(client, "send_command", send_command)

        await client.acquire_token()

        assert client._token.token == "TOK"
        assert client._token.valid_until == 5555
        assert client._token.hash_alg == "SHA256"
        # Second command uses the version-appropriate token request prefix.
        second_cmd = send_command.await_args_list[1].args[0]
        assert second_cmd.startswith(expected_prefix)

    async def test_malformed_token_response_raises(self, client, monkeypatch):
        client._version = 15.0
        client._username = "admin"
        client._password = "secret"

        getkey2 = json.dumps(
            {"LL": {"value": {"key": KNOWN_HMAC_KEY_HEX, "salt": "abc", "hashAlg": "SHA1"}}}
        )
        # Token response is well-formed JSON but lacks the token/validUntil keys.
        bad_token_resp = ll_response(value={"unexpected": 1})
        monkeypatch.setattr(
            client, "send_command", AsyncMock(side_effect=[getkey2, bad_token_resp])
        )

        with pytest.raises(LoxoneException):
            await client.acquire_token()


# --------------------------------------------------------------------------- #
# _refresh_token - builds the version-appropriate refresh command and adopts  #
# the refreshed validity/token (this is the actual refresh wire path, not the #
# loop scheduling verified in T16/T17/T19).                                   #
# --------------------------------------------------------------------------- #
class TestRefreshTokenCommand:
    @pytest.mark.parametrize(
        ("version", "expected_prefix"),
        [(9.0, c.CMD_REFRESH_TOKEN), (15.0, c.CMD_REFRESH_TOKEN_JSON_WEB)],
    )
    async def test_refresh_builds_versioned_command_and_updates_token(
        self, client, monkeypatch, version, expected_prefix
    ):
        client._version = version
        client._username = "admin"
        client._token = LxToken("old-token", 1, "SHA256")
        monkeypatch.setattr(client, "hash_token", AsyncMock(return_value="deadbeef"))
        send_command = AsyncMock(
            return_value=ll_response(value={"validUntil": 8888, "token": "NEWTOK"})
        )
        monkeypatch.setattr(client, "send_command", send_command)

        await client._refresh_token()

        cmd = send_command.await_args.args[0]
        # refreshtoken/ (legacy) vs refreshjwt/ (>= 10.2), then <hash>/<user>.
        assert cmd.startswith(expected_prefix)
        assert cmd.endswith("deadbeef/admin")
        assert client._token.valid_until == 8888
        assert client._token.token == "NEWTOK"
