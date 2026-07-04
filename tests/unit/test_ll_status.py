"""_ll_status_code tests (T21 - accept both 'Code' and 'code')."""

from __future__ import annotations

import pytest

from loxwebsocket.lox_ws_api import _ll_status_code


@pytest.mark.parametrize(
    ("ll", "expected"),
    [
        ({"Code": "200"}, "200"),
        ({"code": "200"}, "200"),
        ({"Code": "404"}, "404"),
        ({"code": "401"}, "401"),
        ({}, None),
        ({"other": "x"}, None),
    ],
)
def test_status_code_casing(ll, expected):
    assert _ll_status_code(ll) == expected


def test_uppercase_code_takes_precedence():
    # If both are present the command-style "Code" wins.
    assert _ll_status_code({"Code": "200", "code": "500"}) == "200"
