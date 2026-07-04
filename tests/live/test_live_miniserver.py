"""Optional smoke test against a real Miniserver.

Marked ``live`` and skipped in CI (``-m "not live"``). Runs locally only when
LOXONE_URL / LOXONE_USERNAME / LOXONE_PASSWORD are provided (e.g. via an
un-committed .env). Credentials must never be hard-coded.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture
def live_credentials():
    url = os.environ.get("LOXONE_URL")
    user = os.environ.get("LOXONE_USERNAME")
    password = os.environ.get("LOXONE_PASSWORD")
    if not (url and user and password):
        pytest.skip(
            "live credentials not configured "
            "(set LOXONE_URL, LOXONE_USERNAME, LOXONE_PASSWORD)"
        )
    return url, user, password


async def test_connect_ping_and_stop(live_credentials):
    from loxwebsocket.lox_ws_api import LoxWs

    url, user, password = live_credentials
    ws = LoxWs()
    await ws.connect(
        user=user,
        password=password,
        loxone_url=url,
        receive_updates=False,
        max_reconnect_attempts=1,
    )
    try:
        assert ws.state == "CONNECTED"
        assert await ws.http_ping() is True
    finally:
        rc = await ws.stop()
        assert rc == 0
