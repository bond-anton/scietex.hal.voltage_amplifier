"""Tests for the generic Modbus driver skeleton.

The device's register map is not transcribed, so every accessor must report the
missing register loudly and without touching the bus. Construction, capability
advertising and connection handling must work regardless.
"""

import asyncio

import pytest
from scietex.hal.serial import ModbusSerialConnectionConfig

try:
    from src.scietex.hal.voltage_amplifier.base import (
        FilterSetting,
        FilterType,
        VoltageAmplifierProtocol,
    )
    from src.scietex.hal.voltage_amplifier.generic.modbus import (
        GenericModbusVoltageAmplifier,
        RegisterMapIncomplete,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base import (
        FilterSetting,
        FilterType,
        VoltageAmplifierProtocol,
    )
    from scietex.hal.voltage_amplifier.generic.modbus import (
        GenericModbusVoltageAmplifier,
        RegisterMapIncomplete,
    )

_GETTERS = [
    ("get_gain", (), "GAIN"),
    ("get_filter", (FilterType.LOW_PASS,), "FILTER_ENABLE"),
    ("get_inverted", (), "INVERT"),
]

_SETTERS = [
    ("set_gain", (10.0,), "GAIN"),
    ("set_filter", (FilterSetting(type=FilterType.LOW_PASS),), "FILTER_TYPE"),
    ("set_inverted", (True,), "INVERT"),
]

_SUPPORT_PROPERTIES = [
    "supports_gain",
    "supports_filters",
    "supports_output_overload",
    "supports_invert_output",
]

_CAN_PROPERTIES = [
    "can_set_gain",
    "can_set_filter",
    "can_set_invert_output",
]


def _config() -> ModbusSerialConnectionConfig:
    """A Modbus RTU configuration; the port is never opened in these tests."""
    return ModbusSerialConnectionConfig(port="/dev/ttyUSB0", framer="RTU")


class NoBusDriver(GenericModbusVoltageAmplifier):
    """Driver that records and rejects every bus access.

    The register map is empty, so a bus call means the driver resolved a
    register it does not have. The call is recorded first so a test can assert
    zero traffic even if an accessor swallows the exception, then raised so the
    failure names the offending call instead of opening a serial port.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bus_calls: list[str] = []

    async def read_registers(self, *args, **kwargs):
        self.bus_calls.append(f"read_registers{args}{kwargs}")
        raise AssertionError(f"unexpected bus read: {args} {kwargs}")

    async def write_register(self, *args, **kwargs):
        self.bus_calls.append(f"write_register{args}{kwargs}")
        raise AssertionError(f"unexpected bus write: {args} {kwargs}")


def _run_scenario(scenario, **driver_kwargs) -> None:
    """Run one scenario on a fresh offline driver inside a live event loop."""

    async def main() -> None:
        driver = NoBusDriver(_config(), **driver_kwargs)
        try:
            await scenario(driver)
            assert not driver.client.connected, "the scenario opened the serial port"
        finally:
            await driver.close()

    asyncio.run(main())


def test_construction_keeps_the_configured_identity():
    """The driver takes its unit id, label and connection config as given."""

    async def scenario(driver) -> None:
        assert driver.address == 7
        assert driver.label == "Test amplifier"
        assert driver.con_params.port == "/dev/ttyUSB0"

    _run_scenario(scenario, address=7, label="Test amplifier")


def test_capabilities_are_declared_on_the_class():
    """The capability set is class data, known before the driver connects."""
    assert "capabilities" in GenericModbusVoltageAmplifier.__dict__
    assert GenericModbusVoltageAmplifier.capabilities is NoBusDriver.capabilities


@pytest.mark.parametrize("property_name", _SUPPORT_PROPERTIES)
def test_no_capability_is_supported_yet(property_name):
    """Without a register map the driver advertises no feature at all."""

    async def scenario(driver) -> None:
        assert getattr(driver, property_name) is False

    _run_scenario(scenario)


@pytest.mark.parametrize("property_name", _CAN_PROPERTIES)
def test_no_capability_is_settable_yet(property_name):
    """A feature that is not supported cannot be written either."""

    async def scenario(driver) -> None:
        assert getattr(driver, property_name) is False

    _run_scenario(scenario)


def test_driver_satisfies_the_protocol():
    """The driver is a runtime-checkable :class:`VoltageAmplifierProtocol`."""

    async def scenario(driver) -> None:
        assert isinstance(driver, VoltageAmplifierProtocol)

    _run_scenario(scenario)


def test_connect_and_close_do_not_touch_the_bus():
    """Opening and closing the transport never reads or writes a register."""

    async def scenario(driver) -> None:
        await driver.connect()
        await driver.close()
        assert driver.bus_calls == []

    _run_scenario(scenario)


def test_read_data_reports_the_first_mandatory_register():
    """The snapshot fails before any traffic, naming the register to fill in."""

    async def scenario(driver) -> None:
        with pytest.raises(RegisterMapIncomplete) as excinfo:
            await driver.read_data()

        assert excinfo.value.register == "INPUT_VOLTAGE"
        assert "INPUT_VOLTAGE" in str(excinfo.value)
        assert "registers.py" in str(excinfo.value)
        assert driver.bus_calls == []

    _run_scenario(scenario)


@pytest.mark.parametrize(("method_name", "args", "register"), _GETTERS)
def test_getter_reports_the_missing_register(method_name, args, register):
    """Every getter resolves its register map before reading the bus."""

    async def scenario(driver) -> None:
        with pytest.raises(RegisterMapIncomplete) as excinfo:
            await getattr(driver, method_name)(*args)

        assert excinfo.value.register == register
        assert "registers.py" in str(excinfo.value)
        assert driver.bus_calls == []

    _run_scenario(scenario)


@pytest.mark.parametrize(("method_name", "args", "register"), _SETTERS)
def test_setter_reports_the_missing_register(method_name, args, register):
    """Every setter resolves its register map before writing the bus."""

    async def scenario(driver) -> None:
        with pytest.raises(RegisterMapIncomplete) as excinfo:
            await getattr(driver, method_name)(*args)

        assert excinfo.value.register == register
        assert "registers.py" in str(excinfo.value)
        assert driver.bus_calls == []

    _run_scenario(scenario)
