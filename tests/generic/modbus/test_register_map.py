"""Tests for the generic Modbus register map.

The map exists to make the missing device manual impossible to paper over: every
register the driver needs is named, none of them carries a wire address, and the
two lookup tables that need manual data stay empty until it is transcribed.
"""

from dataclasses import FrozenInstanceError

import pytest

try:
    from src.scietex.hal.voltage_amplifier.generic.modbus import (
        DEVICE_MAP,
        FILTER_TYPE_CODES,
        STATUS_BITS,
        Register,
        RegisterSpec,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.generic.modbus import (
        DEVICE_MAP,
        FILTER_TYPE_CODES,
        STATUS_BITS,
        Register,
        RegisterSpec,
    )

_REQUIRED_ORDER = [
    "INPUT_VOLTAGE",
    "OUTPUT_VOLTAGE",
    "OUTPUT_CURRENT",
    "GAIN",
    "FILTER_ENABLE",
    "FILTER_TYPE",
    "FILTER_LOW_CUTOFF",
    "FILTER_HIGH_CUTOFF",
    "STATUS",
    "INVERT",
]


def test_register_members_are_the_expected_names_in_capability_order():
    """The enum names every register the driver needs, grouped as designed."""
    assert [register.name for register in Register] == _REQUIRED_ORDER


def test_register_values_are_internal_keys_not_register_addresses():
    """The enum value identifies a map row; it never reaches the wire."""
    assert [int(register) for register in Register] == list(
        range(1, len(_REQUIRED_ORDER) + 1)
    )


def test_map_covers_every_register_and_nothing_else():
    """One row per member, so a new register cannot be left without a spec."""
    assert len(DEVICE_MAP) == len(Register)
    assert set(DEVICE_MAP) == set(Register)


@pytest.mark.parametrize("register", list(Register), ids=lambda r: r.name)
def test_every_row_is_the_neutral_default(register):
    """No row carries an address, a function code, a scale or a signedness."""
    spec = DEVICE_MAP[register]

    assert spec.address is None
    assert spec.function is None
    assert spec.scale == 1.0
    assert spec.offset == 0.0
    assert spec.signed is False
    assert spec.word_count == 1
    assert spec.mapped is False


def test_status_bits_name_no_mask_yet():
    """Status-word masks stay empty until the manual lists them."""
    assert dict(STATUS_BITS) == {}


def test_filter_type_codes_name_no_code_yet():
    """Filter enumeration codes stay empty until the manual lists them."""
    assert dict(FILTER_TYPE_CODES) == {}


def test_mapped_requires_both_coordinates():
    """A row filled in halfway is still unmapped.

    The numbers below are hypothetical: the test only checks that the presence
    of both coordinates, not their value, is what maps a register.
    """
    assert RegisterSpec(address=None, function=3).mapped is False
    assert RegisterSpec(address=8, function=None).mapped is False
    assert RegisterSpec(address=8, function=3).mapped is True


def test_rows_cannot_be_edited_in_place():
    """A row is frozen, so a runtime caller cannot retarget the driver."""
    with pytest.raises(FrozenInstanceError):
        DEVICE_MAP[Register.GAIN].address = 8
