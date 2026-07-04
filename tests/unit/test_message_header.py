"""parse_loxone_message_header_message tests."""

from __future__ import annotations

import pytest
from support.loxone_builders import build_header

pytestmark = pytest.mark.asyncio


class TestHeaderParsing:
    async def test_eight_bytes_is_header(self, client):
        header = build_header(2, 24)
        assert await client.parse_loxone_message_header_message(header) is True
        assert client._current_message_type == 2

    @pytest.mark.parametrize("msg_type", [0, 1, 2, 3, 6, 7])
    async def test_message_type_is_read_from_byte_1(self, client, msg_type):
        await client.parse_loxone_message_header_message(build_header(msg_type, 0))
        assert client._current_message_type == msg_type

    async def test_non_eight_byte_payload_is_not_a_header(self, client):
        assert await client.parse_loxone_message_header_message(b"payload") is False
        assert (
            await client.parse_loxone_message_header_message(b"x" * 24) is False
        )

    async def test_known_ambiguity_eight_byte_payload_looks_like_header(self, client):
        # Documented limitation: an actual 8-byte payload is indistinguishable
        # from a header (the 'estimated' info bit is also not evaluated).
        assert await client.parse_loxone_message_header_message(b"12345678") is True
