"""Byte-level builders for Loxone websocket frames and payloads.

These mirror the binary structures described in the protocol document
(1700_Communicating-with-the-Miniserver.pdf): the 8-byte message header, the
24-byte Value-State events (type 2) and the variable-length Text-State events
(type 3). Keeping the wire format in one place lets every test build realistic
input and predict the exact parser output.
"""

from __future__ import annotations

import json
import struct
import uuid as uuidlib
from collections.abc import Iterable


def build_header(msg_type: int, length: int, *, estimated: bool = False) -> bytes:
    """Build the 8-byte Loxone message header.

    Layout (doc p. 18): ``0x03 | identifier | info | reserved | len(uint32 LE)``.
    The ``estimated`` flag sets bit 1 of the info byte.
    """
    info = 0x01 if estimated else 0x00
    return bytes([0x03, msg_type & 0xFF, info, 0x00]) + struct.pack("<I", length)


def expected_loxone_uuid(raw16: bytes) -> bytes:
    """Return the Loxone UUID string (as ``bytes``) the extractor produces.

    Loxone uses the standard 8-4-4-4-12 UUID string but drops the dash between
    the 4th and 5th group. ``uuid.UUID(bytes_le=...)`` already applies the
    little-endian interpretation of the first three fields, matching the
    extractor's byte reversal.
    """
    if len(raw16) != 16:
        raise ValueError("raw uuid must be 16 bytes")
    parts = str(uuidlib.UUID(bytes_le=raw16)).split("-")
    return ("-".join(parts[:4]) + parts[4]).encode("ascii")


def build_value_states(items: Iterable[tuple[bytes, float]]) -> bytes:
    """Serialize type-2 Value-State events: ``uuid(16) + double(8 LE)`` each."""
    out = bytearray()
    for raw, value in items:
        if len(raw) != 16:
            raise ValueError("raw uuid must be 16 bytes")
        out += raw
        out += struct.pack("<d", value)
    return bytes(out)


def expected_value_dict(items: Iterable[tuple[bytes, float]]) -> dict[bytes, float]:
    """The dict ``parse_message`` should return for the given value states."""
    return {expected_loxone_uuid(raw): float(value) for raw, value in items}


def build_text_states(items: Iterable[tuple[bytes, bytes, bytes]]) -> bytes:
    """Serialize type-3 Text-State events.

    Each entry: ``uuid(16) + uuidIcon(16) + textLength(4 LE) + text`` padded to
    the next 4-byte boundary (doc p. 20).
    """
    out = bytearray()
    for raw, icon, text in items:
        if len(raw) != 16 or len(icon) != 16:
            raise ValueError("raw uuid and icon must be 16 bytes each")
        out += raw
        out += icon
        out += struct.pack("<I", len(text))
        out += text
        chunk = 16 + 16 + 4 + len(text)
        out += b"\x00" * ((-chunk) % 4)
    return bytes(out)


def expected_text_dict(
    items: Iterable[tuple[bytes, bytes, bytes]],
) -> dict[bytes, bytes]:
    """The dict ``parse_type_3_message`` should return (icon is dropped)."""
    return {expected_loxone_uuid(raw): text for raw, _icon, text in items}


def ll_response(
    code: str = "200",
    *,
    value=None,
    control: str | None = None,
    code_key: str = "Code",
) -> str:
    """Build an ``{"LL": {...}}`` text response as the Miniserver would send it.

    ``code_key`` toggles between ``Code`` (command/control responses) and
    ``code`` (token responses) - the protocol is inconsistent about the casing.
    """
    ll: dict = {code_key: code}
    if value is not None:
        ll["value"] = value
    if control is not None:
        ll["control"] = control
    return json.dumps({"LL": ll})
