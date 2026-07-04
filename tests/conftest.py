"""Global pytest fixtures for the loxwebsocket test-suite."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

# Make ``support`` importable as a top-level package from every test module.
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# Load the repo-root .env so live tests pick up LOXONE_* credentials locally.
# Optional dependency: absence must never break collection of non-live tests.
try:
    from dotenv import load_dotenv

    load_dotenv(_TESTS_DIR.parent / ".env")
except ImportError:
    pass

from Crypto.PublicKey import RSA  # noqa: E402
from support.crypto_vectors import (  # noqa: E402
    KNOWN_HMAC_KEY_HEX,
    KNOWN_IV,
    KNOWN_KEY,
    KNOWN_USER_SALT,
)
from support.ws_fake import FakeWSTransport  # noqa: E402

from loxwebsocket.encryption import LxEncryptionHandler, LxJsonKeySalt  # noqa: E402
from loxwebsocket.lox_ws_api import LoxWs  # noqa: E402


@pytest.fixture(autouse=True)
def reset_singleton():
    """Isolate the ``LoxWs`` class-level state between tests.

    ``_event_callbacks`` is a *class* attribute the client mutates in place, so
    without a reset event subscriptions leak across tests. Replacing it with a
    fresh dict (rather than clearing) also drops any per-test instance mutation.
    """
    LoxWs._event_callbacks = {}
    LoxWs._initialized = False
    LoxWs._instance = None
    yield
    LoxWs._event_callbacks = {}


@pytest.fixture
def handler() -> LxEncryptionHandler:
    """A fresh encryption handler with deterministic key/IV."""
    h = LxEncryptionHandler()
    h._key = KNOWN_KEY
    h._iv = KNOWN_IV
    h.reset_salt()
    return h


@pytest.fixture
def client() -> LoxWs:
    """A fresh, fully initialised client with deterministic crypto material."""
    ws = LoxWs()
    ws._encryption_handler._key = KNOWN_KEY
    ws._encryption_handler._iv = KNOWN_IV
    ws._encryption_handler.reset_salt()
    ws._username = "admin"
    ws._password = "secret"
    ws._loxone_url = "http://miniserver.local"
    ws._loxone_ws_url = "ws://miniserver.local"
    ws._max_reconnect_attempts = 1
    return ws


@pytest.fixture
def fake_ws() -> FakeWSTransport:
    return FakeWSTransport()


@pytest.fixture(scope="session")
def rsa_keypair():
    """RSA keypair plus the public key in the Miniserver's getPublicKey format.

    The Miniserver returns the key wrapped in ``CERTIFICATE`` markers; the
    client rewrites those to ``PUBLIC KEY`` before importing it.
    """
    key = RSA.generate(2048)
    pub_pem = key.publickey().export_key().decode("ascii")
    body = "".join(
        line for line in pub_pem.splitlines() if not line.startswith("-----")
    )
    loxone_value = f"-----BEGIN CERTIFICATE-----{body}-----END CERTIFICATE-----"
    return {
        "private": key,
        "public_pem": pub_pem,
        "loxone_value": loxone_value,
    }


def _make_key_salt(hash_alg: str) -> LxJsonKeySalt:
    ks = LxJsonKeySalt()
    ks.key = KNOWN_HMAC_KEY_HEX
    ks.salt = KNOWN_USER_SALT
    ks.hash_alg = hash_alg
    return ks


@pytest.fixture
def key_salt_sha1() -> LxJsonKeySalt:
    return _make_key_salt("SHA1")


@pytest.fixture
def key_salt_sha256() -> LxJsonKeySalt:
    return _make_key_salt("SHA256")


@pytest.fixture
def local_sleep_patch(monkeypatch):
    """Replace the ``asyncio.sleep`` referenced by ``lox_ws_api`` with an
    instant one.

    The patch targets ``loxwebsocket.lox_ws_api.asyncio.sleep`` - i.e. the
    global ``asyncio.sleep`` as seen through that module - so it is effectively
    process-wide for the duration of the test, not scoped to a copy.

    Critically, the replacement still yields to the event loop (via a real
    zero-length sleep) so background tasks keep making progress. A no-op that
    does not yield previously broke regression verification by starving the
    very tasks a test was trying to observe. Recorded delays are exposed on
    ``.calls``.
    """
    real_sleep = asyncio.sleep
    calls: list[float] = []

    async def fake_sleep(delay, result=None):
        calls.append(delay)
        return await real_sleep(0, result)

    monkeypatch.setattr("loxwebsocket.lox_ws_api.asyncio.sleep", fake_sleep)
    fake_sleep.calls = calls  # type: ignore[attr-defined]
    return fake_sleep


@pytest.fixture
def set_tz(monkeypatch):
    """Factory to switch the process timezone (restores it on teardown)."""
    applied = False

    def _set(tz: str):
        nonlocal applied
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        applied = True

    yield _set
    if applied:
        monkeypatch.undo()
        time.tzset()
