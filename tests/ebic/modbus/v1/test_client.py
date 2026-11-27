"""Tests for the EBIC_ADC RS485 driver.

The transport is replaced by a stub that records each request and returns a
canned PDU, so these cover request construction, decoding, capture sequencing and
validation rather than the serial port. No test opens a port. The transaction gap
is disabled so the only delays are the ones the driver itself requests.
"""

import asyncio
from collections.abc import Callable, Iterable

import pytest
from pymodbus.pdu import ModbusPDU
from pymodbus.pdu.bit_message import (
    ReadCoilsRequest,
    ReadCoilsResponse,
    WriteSingleCoilRequest,
    WriteSingleCoilResponse,
)
from pymodbus.pdu.register_message import (
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    ReadInputRegistersRequest,
    ReadInputRegistersResponse,
    WriteSingleRegisterRequest,
    WriteSingleRegisterResponse,
)
from scietex.hal.serial import (
    ModbusOperationError,
    ModbusSerialConnectionConfig,
)

try:
    from src.scietex.hal.voltage_amplifier.base import Capture
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1 import client as client_module
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.client import (
        BoosterTuning,
        EBICModbusV1Booster,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        ADC_REFERENCE_V,
        CAPTURE_POINTS,
        CAPTURE_POLL_INTERVAL_S,
        DEFAULT_DIVIDER_RATIO,
        DEFAULT_SLAVE_ADDRESS,
        EEPROM_WRITE_TIMEOUT_S,
        MAX_CAPTURE_AVERAGING,
        REBOOT_DELAY_S,
        SENTINEL_UNAVAILABLE,
        STATS_WINDOW_SAMPLES,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        CaptureFailed,
        CaptureTimeout,
        UnexpectedResponse,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.identity import (
        DeviceIdentity,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AcquisitionSettings,
        AdcFlags,
        CaptureMetadata,
        CommunicationSettings,
        DeviceStatus,
        StopReason,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base import Capture
    from scietex.hal.voltage_amplifier.ebic.modbus.v1 import client as client_module
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.client import (
        BoosterTuning,
        EBICModbusV1Booster,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        ADC_REFERENCE_V,
        CAPTURE_POINTS,
        CAPTURE_POLL_INTERVAL_S,
        DEFAULT_DIVIDER_RATIO,
        DEFAULT_SLAVE_ADDRESS,
        EEPROM_WRITE_TIMEOUT_S,
        MAX_CAPTURE_AVERAGING,
        REBOOT_DELAY_S,
        SENTINEL_UNAVAILABLE,
        STATS_WINDOW_SAMPLES,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        CaptureFailed,
        CaptureTimeout,
        UnexpectedResponse,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.identity import (
        DeviceIdentity,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AcquisitionSettings,
        AdcFlags,
        CaptureMetadata,
        CommunicationSettings,
        DeviceStatus,
        StopReason,
    )


def _config(timeout: float | None = None) -> ModbusSerialConnectionConfig:
    """A Modbus RTU configuration; the port is never opened in these tests."""
    return ModbusSerialConnectionConfig(
        port="/dev/ttyUSB0",
        framer="RTU",
        timeout=timeout,
    )


class StubBooster(EBICModbusV1Booster):
    """Driver that records each request and returns the next canned response."""

    def __init__(
        self,
        outcomes: Iterable[ModbusPDU | Exception] = (),
        *,
        config: ModbusSerialConnectionConfig | None = None,
        yield_between_requests: bool = False,
    ) -> None:
        self.requests: list[ModbusPDU] = []
        self._outcomes = list(outcomes)
        self._yield_between_requests = yield_between_requests
        super().__init__(
            config if config is not None else _config(),
            tuning=BoosterTuning(transaction_gap_s=0.0),
        )

    async def execute(
        self,
        request: ModbusPDU,
        no_response_expected: bool = False,
        raise_on_error: bool = True,
    ) -> ModbusPDU | None:
        """Record the request and return the next canned response."""
        self.requests.append(request)
        if self._yield_between_requests:
            await asyncio.sleep(0)
        if not self._outcomes:
            raise AssertionError("stub ran out of canned responses")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(
    scenario: Callable[[StubBooster], object],
    *outcomes: ModbusPDU | Exception,
    config: ModbusSerialConnectionConfig | None = None,
    yield_between_requests: bool = False,
) -> None:
    """Run one scenario on a fresh driver inside a live event loop."""

    async def main() -> None:
        driver = StubBooster(
            outcomes,
            config=config,
            yield_between_requests=yield_between_requests,
        )
        try:
            await scenario(driver)
        finally:
            await driver.close()

    asyncio.run(main())


def _input_block() -> list[int]:
    """An 83-register input image, addresses 0-82, with neutral values."""
    return [0] * 83


def _status_responses(block: list[int]) -> tuple[ReadInputRegistersResponse, ...]:
    """The two FC04 responses ``read_data`` and ``get_statistics`` consume."""
    return (
        ReadInputRegistersResponse(registers=block[:50]),
        ReadInputRegistersResponse(registers=block[50:83]),
    )


def _identity_registers(check: int = 7) -> list[int]:
    """The 36-register identity block of a factory device."""
    block = [0] * 36
    block[0:10] = [ord(character) for character in "SCIETEX.RU"]
    block[10:30] = [ord(character) for character in "EBIC_ADC".ljust(20)]
    block[30:33] = [2, 3, 2]
    block[33] = 0x1234
    block[34] = 0x5678
    block[35] = check
    return block


def _physical(code: float) -> float:
    """Expected input voltage of one ADC code, from the ADC and divider constants."""
    return code * ADC_REFERENCE_V / 4095 / DEFAULT_DIVIDER_RATIO


# -- construction and protocol ---------------------------------------------


def test_constructor_defaults():
    """The device answers at address 2, carries its label and uses the divider."""

    async def scenario(driver: StubBooster) -> None:
        assert driver.address == DEFAULT_SLAVE_ADDRESS
        assert driver.label == "EBIC Boost"
        assert driver.divider_ratio == DEFAULT_DIVIDER_RATIO

    _run(scenario)


@pytest.mark.parametrize("ratio", [0.0, -0.6])
def test_tuning_rejects_a_non_positive_divider(ratio):
    """A divider that cannot scale a voltage fails before any driver exists."""
    with pytest.raises(ValueError, match="divider_ratio"):
        BoosterTuning(divider_ratio=ratio)


def test_capability_literal_describes_the_measured_surface():
    """The class literal declares exactly the features the firmware exposes."""
    capabilities = EBICModbusV1Booster.capabilities

    assert capabilities.output_voltage.min_v == 0.0
    assert capabilities.output_voltage.max_v == pytest.approx(
        ADC_REFERENCE_V / DEFAULT_DIVIDER_RATIO
    )
    assert capabilities.output_overload.read is True
    assert capabilities.invert_output.read is True
    assert capabilities.invert_output.write is True

    measurement = capabilities.measurement
    assert measurement.statistics.read is True
    assert measurement.capture.read is True
    assert measurement.capture.write is True
    assert measurement.max_capture_points == CAPTURE_POINTS
    assert measurement.max_capture_averaging == MAX_CAPTURE_AVERAGING

    assert capabilities.gain.gain.options == ()
    assert not capabilities.gain
    assert capabilities.filters.types == ()
    assert not capabilities.filters.enabled
    assert not capabilities.filters.cutoff
    assert capabilities.input_voltage.min_v is None
    assert capabilities.input_voltage.max_v is None


def test_protocol_helpers_follow_the_capability_literal():
    """The synchronous helpers report the same surface as the literal."""

    async def scenario(driver: StubBooster) -> None:
        assert driver.supports_gain is False
        assert driver.supports_filters is False
        assert driver.supports_output_overload is True
        assert driver.supports_invert_output is True
        assert driver.supports_statistics is True
        assert driver.supports_capture is True
        assert driver.can_set_gain is False
        assert driver.can_set_filter is False
        assert driver.can_set_invert_output is True
        assert driver.can_trigger_capture is True

    _run(scenario)


def test_connect_and_close_do_not_touch_the_bus():
    """The serial client opens the port per operation, so connect is a no-op."""

    async def scenario(driver: StubBooster) -> None:
        await driver.connect()
        assert driver.requests == []

    _run(scenario)


# -- state -----------------------------------------------------------------


def test_read_data_issues_two_fc04_reads_and_maps_every_field():
    """The 0-49 and 50-82 windows cover the whole state in two requests."""
    block = _input_block()
    block[36] = 1
    block[38] = int(AdcFlags.STATS_READY)
    block[74] = 1500
    block[76] = 1
    block[77] = 0

    async def scenario(driver: StubBooster) -> None:
        state = await driver.read_data()

        assert state.output_voltage_v == pytest.approx(1500 / 1000 / 0.6)
        assert state.output_overload is True
        assert state.inverted is False
        assert state.input_voltage_v is None
        assert state.output_current_a is None
        assert state.gain is None
        assert state.filters == ()

        low, high = driver.requests
        assert isinstance(low, ReadInputRegistersRequest)
        assert low.address == 0
        assert low.count == 50
        assert low.dev_id == DEFAULT_SLAVE_ADDRESS
        assert isinstance(high, ReadInputRegistersRequest)
        assert high.address == 50
        assert high.count == 33
        assert high.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(scenario, *_status_responses(block))


def test_read_data_uses_the_selected_channel_last_mean():
    """Channel 1 scales PA1's last mean, not PA0's."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[74] = 1500
    block[75] = 3000
    block[77] = 1

    async def scenario(driver: StubBooster) -> None:
        state = await driver.read_data()

        assert state.output_voltage_v == pytest.approx(3000 / 1000 / 0.6)

    _run(scenario, *_status_responses(block))


def test_read_data_returns_none_for_the_unavailable_mean():
    """A sentinel mean is an unavailable value, never 65.535 V."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[74] = SENTINEL_UNAVAILABLE
    block[77] = 0

    async def scenario(driver: StubBooster) -> None:
        state = await driver.read_data()
        assert state.output_voltage_v is None

    _run(scenario, *_status_responses(block))


def test_read_data_returns_none_without_a_fresh_statistics_window():
    """Clearing STATS_READY hides the stale last mean."""
    block = _input_block()
    block[38] = 0
    block[74] = 1500

    async def scenario(driver: StubBooster) -> None:
        state = await driver.read_data()

        assert state.output_voltage_v is None

    _run(scenario, *_status_responses(block))


# -- statistics ------------------------------------------------------------


def test_statistics_decode_the_selected_block_into_physical_volts():
    """Q4 mean, min, max, RMS and the window length reach the statistics."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[43] = STATS_WINDOW_SAMPLES
    block[52] = 2048 * 16
    block[53] = 100
    block[54] = 4000
    block[55] = 9999
    block[56] = 1650
    block[57] = 120
    block[58] = 2400
    block[59] = 7
    block[77] = 0

    async def scenario(driver: StubBooster) -> None:
        statistics = await driver.get_statistics()

        assert statistics.channel == 0
        assert statistics.mean_v == pytest.approx(_physical(2048))
        assert statistics.min_v == pytest.approx(_physical(100))
        assert statistics.max_v == pytest.approx(_physical(4000))
        assert statistics.rms_v == pytest.approx(1.65 / 0.6)
        assert statistics.ac_rms_v == pytest.approx(0.12 / 0.6)
        assert statistics.peak_to_peak_v == pytest.approx(2.4 / 0.6)
        assert statistics.rail_samples == 7
        assert statistics.window_samples == STATS_WINDOW_SAMPLES

        low, high = driver.requests
        assert isinstance(low, ReadInputRegistersRequest)
        assert low.address == 0
        assert low.count == 50
        assert isinstance(high, ReadInputRegistersRequest)
        assert high.address == 50
        assert high.count == 33

    _run(scenario, *_status_responses(block))


def test_statistics_on_channel_one_decode_block_62():
    """The selected channel picks PA1's ten-register block."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[43] = 128
    block[62] = 1024 * 16
    block[63] = 200
    block[64] = 3000
    block[66] = 825
    block[69] = 3
    block[77] = 1

    async def scenario(driver: StubBooster) -> None:
        statistics = await driver.get_statistics()

        assert statistics.channel == 1
        assert statistics.mean_v == pytest.approx(_physical(1024))
        assert statistics.min_v == pytest.approx(_physical(200))
        assert statistics.max_v == pytest.approx(_physical(3000))
        assert statistics.rms_v == pytest.approx(0.825 / 0.6)
        assert statistics.rail_samples == 3
        assert statistics.window_samples == 128

    _run(scenario, *_status_responses(block))


def test_statistics_without_a_fresh_window_carry_no_numbers():
    """Clearing STATS_READY leaves every numeric field None."""
    block = _input_block()
    block[38] = 0
    block[43] = STATS_WINDOW_SAMPLES
    block[52] = 2048 * 16
    block[77] = 1

    async def scenario(driver: StubBooster) -> None:
        statistics = await driver.get_statistics()

        assert statistics.channel == 1
        assert statistics.window_samples == STATS_WINDOW_SAMPLES
        assert statistics.mean_v is None
        assert statistics.min_v is None
        assert statistics.max_v is None
        assert statistics.rms_v is None
        assert statistics.ac_rms_v is None
        assert statistics.peak_to_peak_v is None
        assert statistics.rail_samples is None

    _run(scenario, *_status_responses(block))


# -- inversion -------------------------------------------------------------


@pytest.mark.parametrize(("closed", "inverted"), [(True, False), (False, True)])
def test_get_inverted_negates_the_relay_coil(closed, inverted):
    """A closed coil passes the signal; an open one inverts it."""

    async def scenario(driver: StubBooster) -> None:
        assert await driver.get_inverted() is inverted

        request = driver.requests[0]
        assert isinstance(request, ReadCoilsRequest)
        assert request.address == 0
        assert request.count == 1

    _run(scenario, ReadCoilsResponse(bits=[closed]))


@pytest.mark.parametrize(
    ("inverted", "coil", "encoded"),
    [
        (True, False, b"\x00\x00"),
        (False, True, b"\xff\x00"),
    ],
)
def test_set_inverted_writes_the_relay_coil(inverted, coil, encoded):
    """Inversion writes coil 0 with the encoded state; the echo is checked."""

    async def scenario(driver: StubBooster) -> None:
        await driver.set_inverted(inverted)

        (write,) = driver.requests
        assert isinstance(write, WriteSingleCoilRequest)
        assert write.address == 0
        assert write.bits == [coil]
        assert write.encode()[2:4] == encoded
        assert write.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(scenario, WriteSingleCoilResponse(address=0, bits=[coil]))


# -- acquisition and communication settings --------------------------------


@pytest.mark.parametrize("channel", [0, 1])
def test_select_channel_writes_holding_three(channel):
    """The channel is one holding register write."""

    async def scenario(driver: StubBooster) -> None:
        await driver.select_channel(channel)

        request = driver.requests[0]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 3
        assert request.registers == [channel]

    _run(scenario, WriteSingleRegisterResponse(address=3, registers=[channel]))


@pytest.mark.parametrize("channel", [-1, 2])
def test_select_channel_rejects_an_unknown_channel_before_traffic(channel):
    """Only the two ADC channels exist."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="channel"):
            await driver.select_channel(channel)

        assert driver.requests == []

    _run(scenario)


@pytest.mark.parametrize("averaging", [1, MAX_CAPTURE_AVERAGING])
def test_set_averaging_writes_holding_four(averaging):
    """The averaging depth is one holding register write."""

    async def scenario(driver: StubBooster) -> None:
        await driver.set_averaging(averaging)

        request = driver.requests[0]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 4
        assert request.registers == [averaging]

    _run(
        scenario,
        WriteSingleRegisterResponse(address=4, registers=[averaging]),
    )


@pytest.mark.parametrize("averaging", [0, MAX_CAPTURE_AVERAGING + 1])
def test_set_averaging_rejects_a_value_outside_the_range(averaging):
    """A value outside 1-4000 fails before any bus traffic."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="averaging"):
            await driver.set_averaging(averaging)

        assert driver.requests == []

    _run(scenario)


@pytest.mark.parametrize("mode", [0, 2])
def test_set_rate_mode_writes_holding_five(mode):
    """The rate mode is one holding register write."""

    async def scenario(driver: StubBooster) -> None:
        await driver.set_rate_mode(mode)

        request = driver.requests[0]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 5
        assert request.registers == [mode]

    _run(scenario, WriteSingleRegisterResponse(address=5, registers=[mode]))


@pytest.mark.parametrize("mode", [-1, 3])
def test_set_rate_mode_rejects_an_unknown_mode_before_traffic(mode):
    """The firmware defines three rate modes."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="rate mode"):
            await driver.set_rate_mode(mode)

        assert driver.requests == []

    _run(scenario)


# -- identity --------------------------------------------------------------


def test_read_identity_reads_the_whole_block_once():
    """FC04 reads input registers 0-35 in one request."""

    async def scenario(driver: StubBooster) -> None:
        identity = await driver.read_identity()

        assert identity == DeviceIdentity(
            vendor="SCIETEX.RU",
            product="EBIC_ADC",
            version_major=2,
            version_minor=3,
            version_patch=2,
            serial_number=0x12345678,
        )
        request = driver.requests[0]
        assert isinstance(request, ReadInputRegistersRequest)
        assert request.address == 0
        assert request.count == 36
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(
        scenario,
        ReadInputRegistersResponse(registers=_identity_registers()),
    )


def test_read_identity_rejects_a_wrong_check_register():
    """A block whose check word is not 7 is not an EBIC_ADC identity."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse, match="check"):
            await driver.read_identity()

    _run(
        scenario,
        ReadInputRegistersResponse(registers=_identity_registers(check=8)),
    )


# -- status ----------------------------------------------------------------


def test_read_status_decodes_the_status_block_in_one_frame():
    """Flags, counters, rate, window, age and the reference word share a read."""
    block = [0] * 51
    block[38] = int(AdcFlags.STATS_READY | AdcFlags.RAIL_SAMPLES)
    block[39] = 0x0001
    block[40] = 0x0002
    block[41] = 0x0007
    block[42] = 0xA120
    block[43] = 256
    block[44] = 0
    block[45] = 3
    block[46] = 0
    block[47] = 4
    block[48] = 0
    block[49] = 5
    block[50] = 3301

    async def scenario(driver: StubBooster) -> None:
        status = await driver.read_status()

        assert status == DeviceStatus(
            flags=AdcFlags.STATS_READY | AdcFlags.RAIL_SAMPLES,
            stats_block=0x00010002,
            adc_rate_hz=500_000,
            window_samples=256,
            error_count=3,
            pause_count=4,
            stats_age_ms=5,
        )
        request = driver.requests[0]
        assert isinstance(request, ReadInputRegistersRequest)
        assert request.address == 0
        assert request.count == 51
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(scenario, ReadInputRegistersResponse(registers=block))


# -- persisted settings ----------------------------------------------------


def test_read_acquisition_settings_decodes_the_holding_block():
    """Channel, averaging, mode and the selected sample rate are returned."""

    async def scenario(driver: StubBooster) -> None:
        settings = await driver.read_acquisition_settings()

        assert settings == AcquisitionSettings(
            channel=1,
            averaging=781,
            rate_mode=1,
            sample_rate_hz=500_000,
        )
        request = driver.requests[0]
        assert isinstance(request, ReadHoldingRegistersRequest)
        assert request.address == 0
        assert request.count == 6
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(
        scenario,
        ReadHoldingRegistersResponse(registers=[2, 2, 0, 1, 781, 1]),
    )


def test_read_acquisition_settings_rejects_an_unknown_rate_mode():
    """A mode outside the firmware table cannot name a sample rate."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse, match="rate mode"):
            await driver.read_acquisition_settings()

    _run(
        scenario,
        ReadHoldingRegistersResponse(registers=[2, 2, 0, 0, 1, 3]),
    )


def test_read_communication_settings_decodes_the_address_and_baud():
    """The persisted index maps to its baud rate."""

    async def scenario(driver: StubBooster) -> None:
        settings = await driver.read_communication_settings()

        assert settings == CommunicationSettings(
            slave_address=7,
            baud_index=5,
            baudrate=115200,
        )
        request = driver.requests[0]
        assert isinstance(request, ReadHoldingRegistersRequest)
        assert request.address == 0
        assert request.count == 2
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(scenario, ReadHoldingRegistersResponse(registers=[7, 5]))


def test_read_communication_settings_rejects_an_unknown_baud_index():
    """An index above 5 names no baud rate the firmware supports."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse, match="baud index"):
            await driver.read_communication_settings()

    _run(scenario, ReadHoldingRegistersResponse(registers=[2, 6]))


# -- communication setters -------------------------------------------------


def test_set_slave_address_writes_register_zero_and_waits_for_restart(monkeypatch):
    """The request answers at the old address; the instance adopts the new one."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    async def scenario(driver: StubBooster) -> None:
        await driver.set_slave_address(7)

        assert driver.address == 7
        request = driver.requests[0]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 0
        assert request.registers == [7]
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(
        scenario,
        WriteSingleRegisterResponse(address=0, registers=[7]),
    )
    assert sleeps == [REBOOT_DELAY_S]


@pytest.mark.parametrize("address", [0, 248])
def test_set_slave_address_rejects_an_out_of_range_value(address):
    """The address is validated before any bus traffic."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="slave address"):
            await driver.set_slave_address(address)

        assert driver.requests == []
        assert driver.address == DEFAULT_SLAVE_ADDRESS

    _run(scenario)


def test_set_slave_address_rejects_a_short_transport_timeout():
    """An EEPROM write needs a timeout long enough for the device to persist it."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="timeout"):
            await driver.set_slave_address(7)

        assert driver.requests == []
        assert driver.address == DEFAULT_SLAVE_ADDRESS

    _run(scenario, config=_config(timeout=EEPROM_WRITE_TIMEOUT_S - 0.1))


def test_set_baud_index_writes_register_one_and_adopts_the_rate(monkeypatch):
    """The index is written, then the client rebuilds at the selected rate."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    async def scenario(driver: StubBooster) -> None:
        await driver.set_baud_index(5)

        request = driver.requests[0]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 1
        assert request.registers == [5]
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS
        assert driver.con_params.baudrate == 115200

    _run(
        scenario,
        WriteSingleRegisterResponse(address=1, registers=[5]),
    )
    assert sleeps == [REBOOT_DELAY_S]


@pytest.mark.parametrize("index", [-1, 6])
def test_set_baud_index_rejects_an_unknown_index(index):
    """An index outside 0-5 is rejected before any bus traffic."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="baud index"):
            await driver.set_baud_index(index)

        assert driver.requests == []

    _run(scenario)


def test_set_baud_index_rejects_a_short_transport_timeout():
    """The same EEPROM timeout guard applies to a baud change."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(ValueError, match="timeout"):
            await driver.set_baud_index(5)

        assert driver.requests == []
        assert driver.con_params.baudrate == _config().baudrate

    _run(scenario, config=_config(timeout=EEPROM_WRITE_TIMEOUT_S - 0.1))


# -- serialisation ---------------------------------------------------------


def test_concurrent_reads_do_not_interleave_their_requests():
    """The lock keeps each high-level read's two frames together."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[74] = 1500

    async def scenario(driver: StubBooster) -> None:
        first, second = await asyncio.gather(
            driver.read_data(),
            driver.read_data(),
        )

        assert first == second
        assert [request.address for request in driver.requests] == [0, 50, 0, 50]

    _run(
        scenario,
        *_status_responses(block),
        *_status_responses(block),
        yield_between_requests=True,
    )


def test_transport_errors_still_propagate_through_the_lock():
    """The lock does not swallow a transport failure, and a read is retried."""
    block = _input_block()
    block[38] = int(AdcFlags.STATS_READY)
    block[74] = 1500

    async def scenario(driver: StubBooster) -> None:
        state = await driver.read_data()

        assert state.output_voltage_v == pytest.approx(2.5)
        assert len(driver.requests) == 3

    _run(
        scenario,
        ModbusOperationError("bus"),
        *_status_responses(block),
    )


def test_missing_response_is_an_unexpected_response():
    """A transport that returns nothing cannot be decoded as a register block."""

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse):
            await driver.read_identity()

    _run(scenario, None)


# -- capture ---------------------------------------------------------------

_CAPTURE_CHUNKS: tuple[tuple[int, int], ...] = (
    (128, 60),
    (188, 60),
    (248, 60),
    (308, 60),
    (368, 60),
    (428, 60),
    (488, 60),
    (548, 60),
    (608, 32),
)
"""The nine capture reads: 60 words each but the last 32."""


def _holding_settings(
    *,
    channel: int = 0,
    averaging: int = 8,
    rate_mode: int = 1,
    pending: int = 0,
) -> ReadHoldingRegistersResponse:
    """Holding registers 0-5: address, baud, command, channel, averaging, mode."""
    return ReadHoldingRegistersResponse(
        registers=[DEFAULT_SLAVE_ADDRESS, 2, pending, channel, averaging, rate_mode]
    )


def _poll_responses(flags: int = 0, pending: int = 0):
    """One poll: holding registers 0-2 and the ADC flag word."""
    return (
        ReadHoldingRegistersResponse(
            registers=[DEFAULT_SLAVE_ADDRESS, 2, pending],
        ),
        ReadInputRegistersResponse(registers=[flags]),
    )


def _metadata_registers(  # noqa: PLR0913 - one keyword per metadata word
    *,
    block: int = 0x1234,
    channel: int | None = 0,
    averaging: int | None = 8,
    stop: StopReason = StopReason.NONE,
    adc_error: int = 0,
    dma_error: int = 0,
) -> list[int]:
    """Input registers 72-82 for one capture metadata read."""
    return [
        block >> 16,
        block & 0xFFFF,
        0,
        0,
        0,
        0,
        SENTINEL_UNAVAILABLE if channel is None else channel,
        SENTINEL_UNAVAILABLE if averaging is None else averaging,
        int(stop),
        adc_error,
        dma_error,
    ]


def _capture_codes() -> list[int]:
    """512 interleaved sample codes with one full-scale pair in the middle."""
    codes = [index % 4096 for index in range(512)]
    codes[6] = 4095
    codes[7] = 4095
    return codes


def _chunk_responses(codes: list[int]):
    """The nine FC04 responses that carry the capture block."""
    return tuple(
        ReadInputRegistersResponse(
            registers=codes[start - 128 : start - 128 + count],
        )
        for start, count in _CAPTURE_CHUNKS
    )


def _signature(request: ModbusPDU) -> tuple[type, int | None, int | None]:
    """A request's type, address and read count, with the count of a write None."""
    is_read = isinstance(
        request,
        (ReadHoldingRegistersRequest, ReadInputRegistersRequest),
    )
    return (
        type(request),
        getattr(request, "address", None),
        request.count if is_read else None,
    )


def _capture_sequence(
    *,
    codes: list[int],
    channel: int = 0,
    averaging: int = 8,
    rate_mode: int = 1,
    block: int = 0x1234,
):
    """Every response one successful capture consumes, in request order."""
    metadata = _metadata_registers(
        block=block,
        channel=channel,
        averaging=averaging,
    )
    return (
        _holding_settings(channel=channel, averaging=averaging, rate_mode=rate_mode),
        WriteSingleRegisterResponse(address=2, registers=[1]),
        *_poll_responses(flags=int(AdcFlags.CAPTURE_READY)),
        ReadInputRegistersResponse(registers=metadata),
        *_chunk_responses(codes),
        ReadInputRegistersResponse(registers=list(metadata)),
    )


@pytest.mark.parametrize("channel", [0, 1])
def test_capture_reads_the_sequence_and_decodes_the_captured_channel(channel):
    """Nine frames, 256 points of the reported channel, scaled through the divider."""
    codes = _capture_codes()

    async def scenario(driver: StubBooster) -> None:
        capture = await driver.capture()

        assert isinstance(capture, Capture)
        assert capture.channel == channel
        assert capture.averaging == 8
        assert capture.sample_rate_hz == 500_000
        assert capture.block_number == 0x1234
        assert len(capture.points) == CAPTURE_POINTS
        assert all(isinstance(point, float) for point in capture.points)
        assert capture.points == pytest.approx(
            tuple(_physical(code) for code in codes[channel::2])
        )
        assert capture.points[3] == pytest.approx(
            ADC_REFERENCE_V / DEFAULT_DIVIDER_RATIO
        )
        assert capture.duration_s == pytest.approx(CAPTURE_POINTS * 8 / 500_000)

        expected = [
            (ReadHoldingRegistersRequest, 0, 6),
            (WriteSingleRegisterRequest, 2, None),
            (ReadHoldingRegistersRequest, 0, 3),
            (ReadInputRegistersRequest, 38, 1),
            (ReadInputRegistersRequest, 72, 11),
            *(
                (ReadInputRegistersRequest, start, count)
                for start, count in _CAPTURE_CHUNKS
            ),
            (ReadInputRegistersRequest, 72, 11),
        ]
        actual = [_signature(request) for request in driver.requests]
        assert actual == expected

    _run(scenario, *_capture_sequence(codes=codes, channel=channel))


def test_capture_rejects_a_pending_capture_before_triggering():
    """A capture already running fails on the settings read alone."""
    pending = _holding_settings(pending=1)

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(CaptureFailed, match="still pending"):
            await driver.capture()

        assert len(driver.requests) == 1

    _run(scenario, pending)


def test_capture_rejects_an_unknown_rate_mode():
    """A rate mode outside the firmware table names no sample rate."""
    unknown = _holding_settings(rate_mode=3)

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse, match="rate mode"):
            await driver.capture()

        assert len(driver.requests) == 1

    _run(scenario, unknown)


def test_capture_rejects_an_out_of_range_averaging_depth():
    """The averaging depth is validated before the trigger."""
    excessive = _holding_settings(averaging=MAX_CAPTURE_AVERAGING + 1)

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(CaptureFailed, match="averaging"):
            await driver.capture()

        assert len(driver.requests) == 1

    _run(scenario, excessive)


def test_capture_reports_the_adc_error_with_the_device_codes():
    """An ADC error names the stop reason, both HAL codes and the block."""
    metadata = _metadata_registers(
        block=16,
        stop=StopReason.HAL_ERROR,
        adc_error=3,
        dma_error=4,
    )

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(CaptureFailed) as excinfo:
            await driver.capture()

        message = str(excinfo.value)
        assert "HAL_ERROR" in message
        assert "adc_error=3" in message
        assert "dma_error=4" in message
        assert "block=16" in message

    _run(
        scenario,
        _holding_settings(),
        WriteSingleRegisterResponse(address=2, registers=[1]),
        ReadHoldingRegistersResponse(
            registers=[DEFAULT_SLAVE_ADDRESS, 2, 1],
        ),
        ReadInputRegistersResponse(registers=[int(AdcFlags.ADC_ERROR)]),
        ReadInputRegistersResponse(registers=metadata),
    )


def test_capture_times_out_when_the_buffer_never_becomes_ready(monkeypatch):
    """Polling stops at the deadline instead of waiting for a ready flag."""
    sleeps: list[float] = []

    class FakeClock:
        """Monotonic clock that advances only when the driver reads it."""

        def __init__(self, values: Iterable[float]) -> None:
            self._values = iter(values)

        def monotonic(self) -> float:
            return next(self._values)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        client_module,
        "time",
        FakeClock([0.0, 0.1, 0.2, 0.3, 1.0]),
    )
    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(CaptureTimeout):
            await driver.capture()

        assert sleeps == [CAPTURE_POLL_INTERVAL_S] * 3

    _run(
        scenario,
        _holding_settings(),
        WriteSingleRegisterResponse(address=2, registers=[1]),
        *_poll_responses(),
        *_poll_responses(),
        *_poll_responses(),
        *_poll_responses(),
        ReadInputRegistersResponse(registers=_metadata_registers()),
    )


def test_capture_rejects_a_block_that_changed_while_reading():
    """The verification read must repeat the metadata the points came from."""
    codes = _capture_codes()
    first = _metadata_registers(block=0x100)
    second = _metadata_registers(block=0x101)

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(CaptureFailed, match="changed"):
            await driver.capture()

    _run(
        scenario,
        _holding_settings(),
        WriteSingleRegisterResponse(address=2, registers=[1]),
        *_poll_responses(flags=int(AdcFlags.CAPTURE_READY)),
        ReadInputRegistersResponse(registers=first),
        *_chunk_responses(codes),
        ReadInputRegistersResponse(registers=second),
    )


def test_capture_rejects_a_short_chunk():
    """A chunk that does not carry the requested words cannot be decoded."""
    codes = _capture_codes()
    chunks = list(_chunk_responses(codes))
    chunks[-1] = ReadInputRegistersResponse(registers=codes[-31:])

    async def scenario(driver: StubBooster) -> None:
        with pytest.raises(UnexpectedResponse, match="608"):
            await driver.capture()

    _run(
        scenario,
        _holding_settings(),
        WriteSingleRegisterResponse(address=2, registers=[1]),
        *_poll_responses(flags=int(AdcFlags.CAPTURE_READY)),
        ReadInputRegistersResponse(registers=_metadata_registers()),
        *chunks,
    )


def test_two_captures_serialise_on_the_lock():
    """The second trigger follows the first verification read."""
    codes = _capture_codes()
    sequence = _capture_sequence(codes=codes)

    async def scenario(driver: StubBooster) -> None:
        first, second = await asyncio.gather(driver.capture(), driver.capture())

        assert first == second
        triggers = [
            index
            for index, request in enumerate(driver.requests)
            if isinstance(request, WriteSingleRegisterRequest)
        ]
        assert triggers == [1, len(sequence) + 1]
        assert len(driver.requests) == 2 * len(sequence)

    _run(scenario, *sequence, *sequence, yield_between_requests=True)


def test_trigger_capture_writes_the_command_without_reading():
    """The helper is the trigger write alone, with no wait and no state read."""

    async def scenario(driver: StubBooster) -> None:
        await driver.trigger_capture()

        (request,) = driver.requests
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == 2
        assert request.registers == [1]
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS

    _run(scenario, WriteSingleRegisterResponse(address=2, registers=[1]))


@pytest.mark.parametrize("averaging", [0, SENTINEL_UNAVAILABLE])
def test_read_capture_metadata_maps_the_unavailable_sentinels(averaging):
    """A sentinel channel, a zero depth and block zero decode to None."""
    registers = _metadata_registers(
        block=0,
        channel=None,
        averaging=averaging,
        stop=StopReason.DMA_MISSED,
        adc_error=5,
        dma_error=6,
    )

    async def scenario(driver: StubBooster) -> None:
        metadata = await driver.read_capture_metadata()

        assert metadata == CaptureMetadata(
            channel=None,
            averaging=None,
            block_number=None,
            stop_reason=StopReason.DMA_MISSED,
            adc_error=5,
            dma_error=6,
        )
        request = driver.requests[0]
        assert isinstance(request, ReadInputRegistersRequest)
        assert request.address == 72
        assert request.count == 11

    _run(scenario, ReadInputRegistersResponse(registers=registers))


def test_read_capture_metadata_decodes_a_completed_capture():
    """The block, channel, depth, stop reason and codes reach the caller."""

    async def scenario(driver: StubBooster) -> None:
        metadata = await driver.read_capture_metadata()

        assert metadata == CaptureMetadata(
            channel=1,
            averaging=MAX_CAPTURE_AVERAGING,
            block_number=0x00010002,
            stop_reason=StopReason.HAL_ERROR,
            adc_error=1,
            dma_error=2,
        )

    _run(
        scenario,
        ReadInputRegistersResponse(
            registers=_metadata_registers(
                block=0x00010002,
                channel=1,
                averaging=MAX_CAPTURE_AVERAGING,
                stop=StopReason.HAL_ERROR,
                adc_error=1,
                dma_error=2,
            )
        ),
    )
