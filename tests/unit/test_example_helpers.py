"""Tests for the example logging helpers (T22)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "examples" / "example_usage.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("example_usage", _EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


example = _load_example()


class TestReadable:
    def test_decodes_bytes_and_field_separator(self):
        # b"Pr\xc3\xa4senz" == "Präsenz"; 0x14 is Loxone's field separator.
        assert example._readable(b"Pr\xc3\xa4senz\x14WC") == "Präsenz | WC"

    @pytest.mark.parametrize("value", [42.0, "already-str", 7, None])
    def test_non_bytes_pass_through(self, value):
        assert example._readable(value) == value

    def test_invalid_bytes_use_replacement(self):
        # Must not raise on invalid UTF-8.
        result = example._readable(b"\xff\xfe")
        assert isinstance(result, str)
        assert "\ufffd" in result


class TestReadableData:
    def test_decodes_dict_keys_and_values(self):
        data = {b"uuid-1": 3.5, b"uuid-2": b"Pr\xc3\xa4senz"}
        result = example._readable_data(data)
        assert result == {"uuid-1": 3.5, "uuid-2": "Präsenz"}

    def test_non_dict_delegates_to_readable(self):
        assert example._readable_data(b"Pr\xc3\xa4senz") == "Präsenz"
        assert example._readable_data(1.25) == 1.25
