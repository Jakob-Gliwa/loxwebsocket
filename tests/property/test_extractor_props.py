"""Property-based extractor tests (hypothesis): roundtrips + crash-freedom."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from support.loxone_builders import (
    build_text_states,
    build_value_states,
    expected_loxone_uuid,
    expected_text_dict,
    expected_value_dict,
)

from loxwebsocket.cython_modules.extractor_compatible import (
    convert_bytes_to_uuid,
    parse_message,
    parse_type_3_message,
)

# Value-states are IEEE-754 doubles: NaN is excluded (it breaks == assertions),
# but +/-inf is a legitimate wire value and is deliberately allowed.
_uuid16 = st.binary(min_size=16, max_size=16)
_double = st.floats(allow_nan=False, allow_infinity=True, width=64)


@given(_uuid16)
def test_convert_bytes_to_uuid_shape(raw):
    result = convert_bytes_to_uuid(raw)
    assert result == expected_loxone_uuid(raw)
    assert len(result) == 35


@given(st.lists(st.tuples(_uuid16, _double), min_size=1, max_size=12))
def test_value_state_roundtrip(items):
    # parse_message requires at least one 24-byte packet.
    payload = build_value_states(items)
    assert parse_message(payload) == expected_value_dict(items)


@given(
    st.lists(
        st.tuples(_uuid16, _uuid16, st.binary(max_size=24)),
        max_size=10,
    )
)
def test_text_state_roundtrip(items):
    # An empty list yields an empty payload -> parser returns {}.
    payload = build_text_states(items)
    assert parse_type_3_message(payload) == expected_text_dict(items)


@given(st.binary(max_size=200))
def test_parse_message_only_raises_value_error(data):
    try:
        result = parse_message(data)
    except ValueError:
        return
    assert isinstance(result, dict)


@given(st.binary(max_size=200))
def test_parse_type_3_only_raises_value_error(data):
    try:
        result = parse_type_3_message(data)
    except ValueError:
        return
    assert isinstance(result, dict)
