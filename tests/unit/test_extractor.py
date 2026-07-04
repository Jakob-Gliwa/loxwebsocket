"""Cython extractor tests: convert_bytes_to_uuid, parse_message, parse_type_3.

Runs against every built variant (compatible + optimized when present) to prove
variant parity. On ARM only the compatible variant exists and is used.
"""

from __future__ import annotations

import contextlib
import importlib
import struct

import pytest
from support.loxone_builders import (
    build_text_states,
    build_value_states,
    expected_loxone_uuid,
    expected_text_dict,
    expected_value_dict,
)


def _load_impls():
    impls = {}
    impls["compatible"] = importlib.import_module(
        "loxwebsocket.cython_modules.extractor_compatible"
    )
    # Not built on this arch (e.g. ARM) - parity check is simply skipped.
    with contextlib.suppress(Exception):
        impls["optimized"] = importlib.import_module(
            "loxwebsocket.cython_modules.extractor_optimized"
        )
    return impls


_IMPLS = _load_impls()
_IMPL_PARAMS = list(_IMPLS.items())


@pytest.fixture(params=_IMPL_PARAMS, ids=[name for name, _ in _IMPL_PARAMS])
def ext(request):
    return request.param[1]


class TestConvertBytesToUuid:
    def test_known_layout(self, ext):
        raw = bytes(range(16))
        assert ext.convert_bytes_to_uuid(raw) == expected_loxone_uuid(raw)

    def test_result_is_35_bytes_with_three_dashes(self, ext):
        raw = bytes([0xAB] * 16)
        result = ext.convert_bytes_to_uuid(raw)
        assert len(result) == 35
        assert result.count(b"-") == 3

    @pytest.mark.parametrize("length", [0, 15, 17, 32])
    def test_wrong_length_raises(self, ext, length):
        with pytest.raises(ValueError):
            ext.convert_bytes_to_uuid(bytes(length))


class TestParseMessage:
    def test_single_packet(self, ext):
        items = [(bytes(range(16)), 21.75)]
        assert ext.parse_message(build_value_states(items)) == expected_value_dict(items)

    def test_multiple_packets(self, ext):
        items = [
            (bytes([1] * 16), 1.0),
            (bytes([2] * 16), -2.5),
            (bytes([3] * 16), 3.125),
        ]
        assert ext.parse_message(build_value_states(items)) == expected_value_dict(items)

    @pytest.mark.parametrize("length", [0, 23])
    def test_too_short_raises(self, ext, length):
        with pytest.raises(ValueError):
            ext.parse_message(bytes(length))

    def test_trailing_bytes_are_ignored(self, ext):
        items = [(bytes(range(16)), 7.0)]
        payload = build_value_states(items) + b"\x01\x02\x03\x04\x05"  # 5 extra bytes
        assert ext.parse_message(payload) == expected_value_dict(items)

    def test_double_is_little_endian(self, ext):
        raw = bytes(range(16))
        payload = raw + struct.pack("<d", 1234.5)
        assert ext.parse_message(payload)[expected_loxone_uuid(raw)] == 1234.5


class TestParseType3Message:
    def test_single_entry(self, ext):
        items = [(bytes([1] * 16), bytes([9] * 16), b"Praesenz")]
        assert ext.parse_type_3_message(build_text_states(items)) == expected_text_dict(
            items
        )

    def test_multiple_entries_with_alignment(self, ext):
        items = [
            (bytes([1] * 16), bytes([0] * 16), b"a"),  # forces 4-byte padding
            (bytes([2] * 16), bytes([0] * 16), b"abcd"),
            (bytes([3] * 16), bytes([0] * 16), b"abcde"),
        ]
        assert ext.parse_type_3_message(build_text_states(items)) == expected_text_dict(
            items
        )

    def test_empty_or_short_message_returns_empty_dict(self, ext):
        assert ext.parse_type_3_message(b"") == {}
        assert ext.parse_type_3_message(b"x" * 35) == {}

    def test_text_length_exceeding_buffer_raises(self, ext):
        # 16 (uuid) + 16 (icon) + 4 (length=100) with no text -> overflow.
        payload = bytes(16) + bytes(16) + struct.pack("<I", 100)
        with pytest.raises(ValueError):
            ext.parse_type_3_message(payload)


@pytest.mark.skipif("optimized" not in _IMPLS, reason="optimized variant not built")
class TestVariantParity:
    def test_value_states_parity(self):
        items = [(bytes(range(16)), 42.0), (bytes([7] * 16), -1.0)]
        payload = build_value_states(items)
        assert _IMPLS["compatible"].parse_message(payload) == _IMPLS[
            "optimized"
        ].parse_message(payload)

    def test_text_states_parity(self):
        items = [(bytes([1] * 16), bytes([2] * 16), b"hello world")]
        payload = build_text_states(items)
        assert _IMPLS["compatible"].parse_type_3_message(payload) == _IMPLS[
            "optimized"
        ].parse_type_3_message(payload)
