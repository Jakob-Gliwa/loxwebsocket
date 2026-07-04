"""Encryption & salt regression tests (T1-T8)."""

from __future__ import annotations

import re
import time
from base64 import b64decode
from urllib.parse import unquote

import pytest
from Crypto.Hash import HMAC, SHA1, SHA256
from support.crypto_vectors import (
    KNOWN_IV,
    KNOWN_KEY,
    build_encrypted_control_response,
    decrypt_command,
)

import loxwebsocket.const as c
from loxwebsocket.encryption import LxEncryptionHandler, LxJsonKeySalt

# The ``handler`` fixture is keyed with KNOWN_KEY/KNOWN_IV, which are the
# defaults of ``support.crypto_vectors.decrypt_command`` - so the shared helper
# recovers the plaintext without needing the handler passed in.


# --------------------------------------------------------------------------- #
# T1 - genarate_salt returns a salt (not None)                                #
# --------------------------------------------------------------------------- #
class TestSaltGeneration:
    def test_generate_salt_returns_value(self, handler):
        salt = handler.genarate_salt()
        assert salt is not None
        assert isinstance(salt, str)
        # 16 random bytes hex-encoded -> 32 hex chars.
        assert len(salt) == c.SALT_BYTES * 2
        assert re.fullmatch(r"[0-9a-f]+", salt)

    def test_generate_salt_updates_internal_state(self, handler):
        salt = handler.genarate_salt()
        assert handler._salt == salt

    def test_generate_salt_is_unique(self, handler):
        assert handler.genarate_salt() != handler.genarate_salt()


# --------------------------------------------------------------------------- #
# T2 - encrypted command never contains salt/None or nextSalt/None            #
# --------------------------------------------------------------------------- #
class TestEncryptSaltPrefix:
    @pytest.mark.asyncio
    async def test_first_command_uses_salt_prefix(self, handler):
        enc = await handler.encrypt(c.CMD_ENABLE_UPDATES)
        plaintext = decrypt_command(enc)
        # The salt is a 32-char hex blob - never the literal "None" (the salt
        # bug this guards against). Only assert on the salt itself, not the
        # command payload (which may legitimately contain "None").
        assert re.match(r"^salt/[0-9a-f]{32}/", plaintext)

    @pytest.mark.asyncio
    async def test_command_payload_is_preserved(self, handler):
        enc = await handler.encrypt("jdev/sps/io/AI1/on")
        plaintext = decrypt_command(enc)
        assert plaintext.endswith("jdev/sps/io/AI1/on")


# --------------------------------------------------------------------------- #
# T3 - salt rotation after SALT_MAX_USE_COUNT / SALT_MAX_AGE_SECONDS           #
# --------------------------------------------------------------------------- #
class TestSaltRotation:
    @pytest.mark.asyncio
    async def test_rotation_by_use_count(self, handler):
        first = await handler.encrypt("cmd")
        first_salt = handler._salt
        assert decrypt_command(first).startswith("salt/")

        # Force the use counter to the threshold; the next encrypt must rotate.
        handler._salt_used_count = c.SALT_MAX_USE_COUNT
        rotated = await handler.encrypt("cmd")
        plaintext = decrypt_command(rotated)
        assert plaintext.startswith(f"nextSalt/{first_salt}/{handler._salt}/")
        assert handler._salt != first_salt

    @pytest.mark.asyncio
    async def test_rotation_by_age(self, handler):
        await handler.encrypt("cmd")
        first_salt = handler._salt
        handler._salt_time_stamp = round(time.time()) - c.SALT_MAX_AGE_SECONDS - 1
        rotated = await handler.encrypt("cmd")
        assert decrypt_command(rotated).startswith(f"nextSalt/{first_salt}/")

    @pytest.mark.asyncio
    async def test_stable_salt_below_threshold(self, handler):
        await handler.encrypt("cmd")
        first_salt = handler._salt
        second = await handler.encrypt("cmd")
        assert decrypt_command(second).startswith(f"salt/{first_salt}/")


# --------------------------------------------------------------------------- #
# T4 - reset_salt discards all salt state                                     #
# --------------------------------------------------------------------------- #
class TestResetSalt:
    @pytest.mark.asyncio
    async def test_reset_clears_state(self, handler):
        await handler.encrypt("cmd")
        assert handler._salt != ""
        handler.reset_salt()
        assert handler._salt == ""
        assert handler._salt_used_count == 0
        assert handler._salt_time_stamp == 0

    @pytest.mark.asyncio
    async def test_next_encrypt_after_reset_starts_fresh(self, handler):
        # Push into nextSalt territory, then reset -> back to a fresh salt/.
        await handler.encrypt("cmd")
        handler._salt_used_count = c.SALT_MAX_USE_COUNT
        rotated = await handler.encrypt("cmd")
        assert decrypt_command(rotated).startswith("nextSalt/")

        handler.reset_salt()
        fresh = await handler.encrypt("cmd")
        plaintext = decrypt_command(fresh)
        assert plaintext.startswith("salt/")
        assert not plaintext.startswith("nextSalt/")


# --------------------------------------------------------------------------- #
# T5 - decrypt_control_response uses ZeroBytePadding (not PKCS7)               #
# --------------------------------------------------------------------------- #
class TestDecryptControlResponse:
    @pytest.mark.asyncio
    async def test_zero_byte_padding_roundtrip(self, handler):
        plaintext = "jdev/sps/io/012345/state"
        cipher_b64 = build_encrypted_control_response(plaintext, KNOWN_KEY, KNOWN_IV)
        result = await handler.decrypt_control_response(cipher_b64)
        assert result == plaintext
        assert not result.endswith("\x00")

    @pytest.mark.asyncio
    async def test_accepts_jdev_enc_prefixed_input(self, handler):
        plaintext = "hello world"
        cipher_b64 = build_encrypted_control_response(plaintext, KNOWN_KEY, KNOWN_IV)
        result = await handler.decrypt_control_response(f"jdev/sys/enc/{cipher_b64}")
        assert result == plaintext

    @pytest.mark.asyncio
    async def test_block_aligned_plaintext(self, handler):
        plaintext = "0123456789abcdef"  # exactly one AES block, no padding added
        cipher_b64 = build_encrypted_control_response(plaintext, KNOWN_KEY, KNOWN_IV)
        assert await handler.decrypt_control_response(cipher_b64) == plaintext

    @pytest.mark.asyncio
    async def test_known_edge_case_trailing_nul_is_stripped(self, handler):
        # Documented limitation: real trailing NULs in the payload are
        # indistinguishable from padding and get stripped.
        plaintext = "value\x00\x00"
        cipher_b64 = build_encrypted_control_response(plaintext, KNOWN_KEY, KNOWN_IV)
        assert await handler.decrypt_control_response(cipher_b64) == "value"

    @pytest.mark.asyncio
    async def test_invalid_input_raises_value_error(self, handler):
        with pytest.raises(ValueError):
            await handler.decrypt_control_response("!!!not-base64!!!")


# --------------------------------------------------------------------------- #
# T6 - cipher is URI-component-encoded (+ -> %2B, = -> %3D, / -> %2F)          #
# --------------------------------------------------------------------------- #
class TestCipherUriEncoding:
    @pytest.mark.asyncio
    async def test_no_raw_base64_specials_in_suffix(self, handler):
        enc = await handler.encrypt("jdev/sps/io/AI1/on")
        suffix = enc[len(c.CMD_ENCRYPT_CMD):]
        for raw in ("+", "/", "="):
            assert raw not in suffix, f"unescaped {raw!r} in cipher suffix"

    @pytest.mark.asyncio
    async def test_suffix_roundtrips_via_unquote(self, handler):
        enc = await handler.encrypt("cmd")
        suffix = enc[len(c.CMD_ENCRYPT_CMD):]
        # unquote must recover valid base64 (otherwise decrypt would fail).
        assert b64decode(unquote(suffix))


# --------------------------------------------------------------------------- #
# T7 - hash algorithm selection SHA1 vs SHA256                                #
# --------------------------------------------------------------------------- #
class TestHashAlgorithmSelection:
    @pytest.mark.parametrize("alg", ["SHA1", "SHA256"])
    def test_password_hash_matches_selected_algorithm(self, handler, alg, request):
        key_salt = request.getfixturevalue(
            "key_salt_sha1" if alg == "SHA1" else "key_salt_sha256"
        )
        password = "hunter2"
        digestmod = SHA1 if alg == "SHA1" else SHA256
        expected = (
            digestmod.new(f"{password}:{key_salt.salt}".encode()).hexdigest().upper()
        )
        assert handler.generate_password_hash(key_salt, password) == expected

    @pytest.mark.parametrize("alg", ["SHA1", "SHA256"])
    def test_hash_credentials_matches_selected_algorithm(self, handler, alg, request):
        import binascii

        key_salt = request.getfixturevalue(
            "key_salt_sha1" if alg == "SHA1" else "key_salt_sha256"
        )
        user, password = "admin", "hunter2"
        digestmod = SHA1 if alg == "SHA1" else SHA256
        pwd_hash = (
            digestmod.new(f"{password}:{key_salt.salt}".encode()).hexdigest().upper()
        )
        expected = HMAC.new(
            binascii.unhexlify(key_salt.key),
            f"{user}:{pwd_hash}".encode(),
            digestmod,
        ).hexdigest()
        assert handler.hash_credentials(key_salt, password, user) == expected


# --------------------------------------------------------------------------- #
# read_user_salt_responce - operates on an already-parsed dict (Variante 3)    #
# --------------------------------------------------------------------------- #
class TestReadUserSaltResponse:
    def _payload(self, **value):
        return {"LL": {"value": value}}

    def test_parses_key_salt_and_hash_alg(self):
        ks = LxJsonKeySalt()
        ks.read_user_salt_responce(
            self._payload(key="aabb", salt="1234", hashAlg="SHA256")
        )
        assert ks.key == "aabb"
        assert ks.salt == "1234"
        assert ks.hash_alg == "SHA256"

    def test_hash_alg_defaults_to_sha1_when_missing(self):
        ks = LxJsonKeySalt()
        ks.read_user_salt_responce(self._payload(key="aabb", salt="1234"))
        assert ks.hash_alg == "SHA1"

    def test_unrecognised_hash_alg_raises(self):
        ks = LxJsonKeySalt()
        with pytest.raises(ValueError, match="Unrecognised hash algorithm"):
            ks.read_user_salt_responce(
                self._payload(key="aabb", salt="1234", hashAlg="MD5")
            )


# --------------------------------------------------------------------------- #
# T8 - session key is NOT URI-encoded (raw base64 for the ws keyexchange)      #
# --------------------------------------------------------------------------- #
class TestSessionKeyEncoding:
    @pytest.mark.asyncio
    async def test_session_key_is_raw_base64(self, handler, rsa_keypair, monkeypatch):
        async def fake_get_public_key(username, password, loxone_url):
            return rsa_keypair["public_pem"]

        monkeypatch.setattr(
            LxEncryptionHandler, "get_public_key", staticmethod(fake_get_public_key)
        )
        session_key = await handler.generate_session_key("u", "p", "http://ms")
        # Raw base64: no percent-encoding was applied.
        assert "%" not in session_key
        # Decodes to a full RSA block (2048-bit key -> 256 bytes).
        assert len(b64decode(session_key)) == 256


# --------------------------------------------------------------------------- #
# get_public_key: HTTP retrieval + CERTIFICATE -> PUBLIC KEY rewrite          #
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, status=200, json_data=None, content_type_error=False):
        self.status = status
        self._json = json_data
        self._content_type_error = content_type_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, loads=None):
        if self._content_type_error:
            import aiohttp

            raise aiohttp.ContentTypeError(request_info=None, history=())
        return self._json


class _FakeSession:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, *args, **kwargs):
        return self._response


def _patch_session(monkeypatch, response):
    def factory(*args, **kwargs):
        return _FakeSession(response)

    monkeypatch.setattr("loxwebsocket.encryption.aiohttp.ClientSession", factory)


class TestGetPublicKey:
    @pytest.mark.asyncio
    async def test_rewrites_certificate_markers(self, monkeypatch):
        value = "-----BEGIN CERTIFICATE-----ABCD-----END CERTIFICATE-----"
        _patch_session(monkeypatch, _FakeResponse(200, {"LL": {"value": value}}))
        result = await LxEncryptionHandler.get_public_key("u", "p", "http://ms")
        assert result.startswith("-----BEGIN PUBLIC KEY-----\n")
        assert result.rstrip().endswith("-----END PUBLIC KEY-----")
        assert "CERTIFICATE" not in result

    @pytest.mark.asyncio
    async def test_non_200_raises(self, monkeypatch):
        _patch_session(monkeypatch, _FakeResponse(401, {}))
        with pytest.raises(ValueError):
            await LxEncryptionHandler.get_public_key("u", "p", "http://ms")

    @pytest.mark.asyncio
    async def test_missing_ll_value_raises(self, monkeypatch):
        _patch_session(monkeypatch, _FakeResponse(200, {"LL": {}}))
        with pytest.raises(ValueError):
            await LxEncryptionHandler.get_public_key("u", "p", "http://ms")

    @pytest.mark.asyncio
    async def test_empty_key_raises(self, monkeypatch):
        _patch_session(monkeypatch, _FakeResponse(200, {"LL": {"value": ""}}))
        with pytest.raises(ValueError):
            await LxEncryptionHandler.get_public_key("u", "p", "http://ms")

    @pytest.mark.asyncio
    async def test_non_json_raises(self, monkeypatch):
        _patch_session(monkeypatch, _FakeResponse(200, content_type_error=True))
        with pytest.raises(ValueError):
            await LxEncryptionHandler.get_public_key("u", "p", "http://ms")
