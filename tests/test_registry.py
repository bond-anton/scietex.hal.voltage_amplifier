"""Tests for the driver entry point registry."""

import re

import pytest

from scietex.hal.voltage_amplifier.ebic.modbus.v1 import EBICModbusV1Booster

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


def test_discover_drivers_advertises_the_ebic_booster():
    """The installed package advertises exactly the EBIC_ADC booster driver."""
    # The entry point loads the class through its installed module path, so the
    # comparison uses that same import rather than a ``src.`` alias.
    assert discover_drivers() == {"ebic_modbus_v1_boost": EBICModbusV1Booster}


def test_resolve_driver_returns_the_advertised_class():
    """The entry point name resolves to the driver class it names."""
    assert resolve_driver("ebic_modbus_v1_boost") is EBICModbusV1Booster


def test_resolve_unknown_driver_raises_with_available_names():
    """An unknown name raises ``ValueError`` and lists what is available."""
    with pytest.raises(
        ValueError,
        match=re.escape("Unknown voltage amplifier driver 'nope'"),
    ) as excinfo:
        resolve_driver("nope")

    message = str(excinfo.value)
    assert "scietex.hal.voltage_amplifier.drivers" in message
    assert "available: ebic_modbus_v1_boost" in message
