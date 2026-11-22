"""Tests for the driver entry point registry."""

import re

import pytest

try:
    from src.scietex.hal.voltage_amplifier.registry import (
        ENTRY_POINT_GROUP,
        discover_drivers,
        resolve_driver,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.registry import (
        ENTRY_POINT_GROUP,
        discover_drivers,
        resolve_driver,
    )


def test_entry_point_group_name():
    """The group name is the documented constant."""
    assert ENTRY_POINT_GROUP == "scietex.hal.voltage_amplifier.drivers"


def test_discover_drivers_is_empty():
    """No driver is advertised by the package yet."""
    assert discover_drivers() == {}


def test_resolve_unknown_driver_raises_with_available_names():
    """An unadvertised name raises ``ValueError`` and lists what is available."""
    with pytest.raises(
        ValueError,
        match=re.escape("Unknown voltage amplifier driver 'nope'"),
    ) as excinfo:
        resolve_driver("nope")

    message = str(excinfo.value)
    assert "scietex.hal.voltage_amplifier.drivers" in message
    assert "available: none" in message
