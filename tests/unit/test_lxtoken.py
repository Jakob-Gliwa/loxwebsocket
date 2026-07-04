"""LxToken tests (T18 - timezone/platform independence + accessors)."""

from __future__ import annotations

import datetime as dt

import pytest
import time_machine

from loxwebsocket.lxtoken import _LOXONE_EPOCH, LxToken

# 2009-01-01 UTC in Unix seconds.
_EXPECTED_EPOCH = int(dt.datetime(2009, 1, 1, tzinfo=dt.timezone.utc).timestamp())
# Fixed "now" for deterministic assertions.
_NOW = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
_NOW_UNIX = int(_NOW.timestamp())


def test_loxone_epoch_constant():
    assert _LOXONE_EPOCH == _EXPECTED_EPOCH


class TestGetSecondsToExpire:
    @pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Asia/Kolkata"])
    def test_timezone_independent(self, set_tz, tz):
        set_tz(tz)
        valid_until = 500_000_000
        with time_machine.travel(_NOW, tick=False):
            token = LxToken(valid_until=valid_until)
            assert (
                token.get_seconds_to_expire()
                == _EXPECTED_EPOCH + valid_until - _NOW_UNIX
            )

    def test_expired_token_returns_negative(self):
        with time_machine.travel(_NOW, tick=False):
            # valid_until in the far past relative to 2024.
            token = LxToken(valid_until=1)
            assert token.get_seconds_to_expire() < 0

    def test_default_valid_until_is_epoch_relative(self):
        with time_machine.travel(_NOW, tick=False):
            token = LxToken()
            assert token.get_seconds_to_expire() == _EXPECTED_EPOCH - _NOW_UNIX


class TestAccessors:
    def test_defaults(self):
        token = LxToken()
        assert token.token == ""
        assert token.valid_until == 0
        assert token.hash_alg == "SHA1"

    def test_constructor_values(self):
        token = LxToken("tok", 123, "SHA256")
        assert token.token == "tok"
        assert token.valid_until == 123
        assert token.hash_alg == "SHA256"

    # Setters are covered by tests/api/test_public_api.py::TestLxTokenContract.
