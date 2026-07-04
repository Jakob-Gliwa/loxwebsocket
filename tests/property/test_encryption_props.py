"""Property-based encryption tests (hypothesis): roundtrips + salt invariants."""

from __future__ import annotations

import asyncio
import re

from hypothesis import given, settings
from hypothesis import strategies as st
from support.crypto_vectors import (
    KNOWN_IV,
    KNOWN_KEY,
    build_encrypted_control_response,
    decrypt_command,
)

from loxwebsocket.encryption import LxEncryptionHandler

# Printable ASCII without trailing-NUL ambiguity (ZeroBytePadding strips NULs).
_command_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=80
)


def _fresh_handler() -> LxEncryptionHandler:
    handler = LxEncryptionHandler()
    handler._key = KNOWN_KEY
    handler._iv = KNOWN_IV
    handler.reset_salt()
    return handler


@given(_command_text)
def test_encrypt_command_roundtrip(command):
    handler = _fresh_handler()
    enc = asyncio.run(handler.encrypt(command))
    plaintext = decrypt_command(enc)
    # Plaintext is "salt/<salt>/<command>" (trailing NUL/padding stripped).
    assert plaintext.startswith("salt/")
    assert plaintext.endswith(command)


@given(_command_text)
def test_decrypt_control_response_roundtrip(plaintext):
    handler = _fresh_handler()
    cipher_b64 = build_encrypted_control_response(plaintext, KNOWN_KEY, KNOWN_IV)
    result = asyncio.run(handler.decrypt_control_response(cipher_b64))
    # Trailing NULs cannot survive ZeroBytePadding - exclude that one ambiguity.
    assert result == plaintext.rstrip("\x00")


@given(st.lists(_command_text, min_size=1, max_size=40))
@settings(max_examples=50)
def test_salt_prefix_is_always_valid(commands):
    handler = _fresh_handler()
    # The salt(s) are always 32-char hex blobs - never the literal "None"
    # (the regression this guards against). Anchoring on hex also implicitly
    # excludes "None", so no separate substring check is needed: the command
    # payload that follows may legitimately contain the word "None".
    pattern = re.compile(r"^(salt/[0-9a-f]{32}/|nextSalt/[0-9a-f]{32}/[0-9a-f]{32}/)")
    for command in commands:
        enc = asyncio.run(handler.encrypt(command))
        plaintext = decrypt_command(enc)
        assert pattern.match(plaintext), plaintext
