"""Tests for the amplifier state snapshot."""

import msgspec
import pytest

try:
    from src.scietex.hal.voltage_amplifier.base.capabilities import FilterType
    from src.scietex.hal.voltage_amplifier.base.data import (
        AmplifierState,
        FilterSetting,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base.capabilities import FilterType
    from scietex.hal.voltage_amplifier.base.data import AmplifierState, FilterSetting


_EXPECTED_FIELD_ORDER = [
    "input_voltage_v",
    "output_voltage_v",
    "output_current_a",
    "gain",
    "filters",
    "output_overload",
    "inverted",
]

_EXPECTED_FIELDS = set(_EXPECTED_FIELD_ORDER)


def test_default_state_reports_nothing():
    """A default snapshot carries every scalar as ``None`` and no filters."""
    state = AmplifierState()

    field_names = {field.name for field in msgspec.structs.fields(AmplifierState)}
    assert field_names == _EXPECTED_FIELDS
    assert state.filters == ()
    for name in sorted(_EXPECTED_FIELDS - {"filters"}):
        assert getattr(state, name) is None


def test_field_order_is_stable():
    """Declared order is the serialised order, so appends do not shuffle fields."""
    field_names = [field.name for field in msgspec.structs.fields(AmplifierState)]

    assert field_names == _EXPECTED_FIELD_ORDER


def test_to_builtins_serialises_filter_type_as_string():
    """The snapshot converts to plain builtins with the enum's wire spelling."""
    state = AmplifierState(
        gain=10.0,
        filters=(
            FilterSetting(
                type=FilterType.LOW_PASS,
                enabled=True,
                low_cutoff_hz=10.0,
                high_cutoff_hz=1e3,
            ),
        ),
        inverted=False,
    )

    assert msgspec.to_builtins(state) == {
        "input_voltage_v": None,
        "output_voltage_v": None,
        "output_current_a": None,
        "gain": 10.0,
        "filters": (
            {
                "type": "low_pass",
                "enabled": True,
                "low_cutoff_hz": 10.0,
                "high_cutoff_hz": 1e3,
            },
        ),
        "output_overload": None,
        "inverted": False,
    }
    assert list(msgspec.to_builtins(state)) == _EXPECTED_FIELD_ORDER


def test_filter_setting_is_frozen():
    """A filter entry cannot be mutated in place."""
    setting = FilterSetting(type=FilterType.LOW_PASS)

    # msgspec frozen structs raise AttributeError, not dataclasses.FrozenInstanceError.
    with pytest.raises(AttributeError, match="immutable type"):
        setting.enabled = True


def test_state_is_frozen():
    """A snapshot cannot be mutated in place."""
    state = AmplifierState()

    with pytest.raises(AttributeError, match="immutable type"):
        state.gain = 1.0


def test_false_is_distinct_from_none():
    """A present-but-off flag stays ``False`` while an unreported one stays ``None``."""
    assert AmplifierState(inverted=False).inverted is False
    assert AmplifierState().inverted is None


def test_unity_gain_is_distinct_from_an_unreported_gain():
    """``1.0`` says the gain is unity; ``None`` says nothing is known."""
    base = AmplifierState()
    unity = msgspec.structs.replace(base, gain=1.0)

    assert unity.gain == 1.0
    assert unity != base
    assert base.gain is None
