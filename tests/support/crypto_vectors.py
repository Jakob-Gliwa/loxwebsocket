"""Deterministic crypto material and helpers for encryption tests.

The command encryption uses AES-256-CBC with ZeroBytePadding and Base64 (doc
p. 24). These helpers let tests build ciphertext exactly the way a Miniserver
would, so the client's decrypt path can be verified without a live server.
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from urllib.parse import unquote

from Crypto.Cipher import AES

import loxwebsocket.const as c

# Fixed 32-byte AES key / 16-byte IV so failures are reproducible.
KNOWN_KEY = bytes(range(32))
KNOWN_IV = bytes(range(16))

# 16 raw bytes -> 32 hex chars; valid input for binascii.unhexlify in HMAC.
KNOWN_HMAC_KEY_HEX = "0123456789abcdef0123456789abcdef"
KNOWN_USER_SALT = "1a2b3c4d"


def zero_pad(data: bytes) -> bytes:
    """Pad ``data`` with NUL bytes up to the AES block size (ZeroBytePadding)."""
    return data + b"\x00" * ((-len(data)) % 16)


def aes_encrypt_zero_padded(plaintext: str, key: bytes = KNOWN_KEY, iv: bytes = KNOWN_IV) -> bytes:
    """Encrypt ``plaintext`` the way the Miniserver encrypts a control response."""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(zero_pad(plaintext.encode("utf-8")))


def build_encrypted_control_response(plaintext: str, key: bytes = KNOWN_KEY, iv: bytes = KNOWN_IV) -> str:
    """Return Base64 ciphertext accepted by ``decrypt_control_response``.

    The method strips an optional ``jdev/sys/enc/`` prefix, so returning the
    bare Base64 is sufficient.
    """
    return b64encode(aes_encrypt_zero_padded(plaintext, key, iv)).decode("ascii")


def decrypt_command(enc_command: str, key: bytes = KNOWN_KEY, iv: bytes = KNOWN_IV) -> str:
    """Reverse ``LxEncryptionHandler.encrypt`` to recover the plaintext command.

    Undoes the ``jdev/sys/enc/`` prefix, URI-component encoding, Base64 and
    AES-256-CBC ZeroBytePadding.
    """
    suffix = enc_command.split(c.CMD_ENCRYPT_CMD)[-1]
    ciphertext = b64decode(unquote(suffix))
    plaintext = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
    return plaintext.rstrip(b"\x00").decode("utf-8")
