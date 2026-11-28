"""Tests for the EBIC_ADC Modbus/TCP driver.

``AsyncModbusTcpClient`` is replaced by a fake gateway holding a register image,
so these exercise the real device layer inherited from the serial driver --
snapshot, statistics, capture, identity and status -- over the TCP request path
rather than a socket. The transport-specific behaviour (connection lifecycle,
unit-id stamping, gateway error mapping) is covered alongside.
"""

import asyncio
from logging import getLogger

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse
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
from scietex.hal.serial import ModbusOperationError

try:
    from src.scietex.hal.voltage_amplifier.base import VoltageAmplifierProtocol
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1 import (
        EBICModbusV1Booster,
        EBICModbusV1BoosterTcp,
        GatewayBusy,
        GatewayTargetFailed,
        client_tcp,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1 import client as client_module
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.client import BoosterTuning
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        ADC_FULL_SCALE_CODE,
        ADC_REFERENCE_V,
        CAPTURE_POINTS,
        DEFAULT_DIVIDER_RATIO,
        DEFAULT_SLAVE_ADDRESS,
        EEPROM_WRITE_TIMEOUT_S,
        READ_ATTEMPTS,
        RELAY_COIL,
        SENTINEL_UNAVAILABLE,
        HoldingRegister,
        InputRegister,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AdcFlags,
        StopReason,
    )
    from src.scietex.hal.voltage_amplifier.registry import resolve_driver
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base import VoltageAmplifierProtocol
    from scietex.hal.voltage_amplifier.ebic.modbus.v1 import (
        EBICModbusV1Booster,
        EBICModbusV1BoosterTcp,
        GatewayBusy,
        GatewayTargetFailed,
        client_tcp,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1 import client as client_module
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.client import BoosterTuning
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        ADC_FULL_SCALE_CODE,
        ADC_REFERENCE_V,
        CAPTURE_POINTS,
        DEFAULT_DIVIDER_RATIO,
        DEFAULT_SLAVE_ADDRESS,
        EEPROM_WRITE_TIMEOUT_S,
        READ_ATTEMPTS,
        RELAY_COIL,
        SENTINEL_UNAVAILABLE,
        HoldingRegister,
        InputRegister,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AdcFlags,
        StopReason,
    )
    from scietex.hal.voltage_amplifier.registry import resolve_driver

_INPUT_REGISTER_COUNT = InputRegister.CAPTURE_END + 1
"""Input-register image size: the identity block through the capture buffer."""

_CAPTURE_REGISTERS = CAPTURE_POINTS * 2
"""Input registers the capture buffer occupies, two per point."""


def _physical(code: float) -> float:
    """Expected input voltage of one ADC code, from the ADC and divider constants."""
    return code * ADC_REFERENCE_V / ADC_FULL_SCALE_CODE / DEFAULT_DIVIDER_RATIO


def _millivolts(value: float) -> float:
    """Expected input voltage of a millivolt reading after the divider."""
    return value / 1000 / DEFAULT_DIVIDER_RATIO


class _FakeTcpClient:
    """A fake gateway exposing a register image through the pymodbus interface."""

    default_connect_result = True
    """Connect outcome for clients built later; tests patch it on the subclass."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 502,
        timeout: float = 3.0,
        retries: int = 3,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.connected = False
        self.connect_calls = 0
        self.closed = False
        self.connect_result = type(self).default_connect_result
        self.error: Exception | None = None
        self.override: object | None = None
        self.requests: list[tuple[bool, object]] = []
        self.input_registers = [0] * _INPUT_REGISTER_COUNT
        self.holding_registers = [0] * (HoldingRegister.RATE_MODE + 1)
        self.coils = [False] * (RELAY_COIL + 1)
        _load_device(self)

    async def connect(self) -> bool:
        self.connect_calls += 1
        self.connected = self.connect_result
        return self.connect_result

    def close(self) -> None:
        self.closed = True
        self.connected = False

    async def execute(self, no_response_expected: bool, request):
        self.requests.append((no_response_expected, request))
        if self.error is not None:
            raise self.error
        self._apply(request)
        if no_response_expected:
            return None
        if self.override is not None:
            response, self.override = self.override, None
            return response
        return self._respond(request)

    def _apply(self, request) -> None:
        """Update the register image the way the firmware would."""
        if isinstance(request, WriteSingleRegisterRequest):
            register = request.address
            value = request.registers[0]
            self.holding_registers[register] = value
            if register == HoldingRegister.CAPTURE_COMMAND and value:
                # The fake finishes a capture at once: the command clears and
                # the buffer is marked ready before the first poll.
                self.holding_registers[register] = 0
                self.input_registers[InputRegister.ADC_FLAGS] |= int(
                    AdcFlags.CAPTURE_READY
                )
        elif isinstance(request, WriteSingleCoilRequest):
            self.coils[request.address] = bool(request.bits[0])

    def _respond(self, request):
        """Answer a read from the register image, or raise on an unknown request."""
        if isinstance(request, ReadInputRegistersRequest):
            start, count = request.address, request.count
            return ReadInputRegistersResponse(
                registers=self.input_registers[start : start + count]
            )
        if isinstance(request, ReadHoldingRegistersRequest):
            start, count = request.address, request.count
            return ReadHoldingRegistersResponse(
                registers=self.holding_registers[start : start + count]
            )
        if isinstance(request, ReadCoilsRequest):
            start, count = request.address, request.count
            return ReadCoilsResponse(bits=self.coils[start : start + count])
        if isinstance(request, WriteSingleRegisterRequest):
            return WriteSingleRegisterResponse(
                address=request.address, registers=list(request.registers)
            )
        if isinstance(request, WriteSingleCoilRequest):
            return WriteSingleCoilResponse(
                address=request.address, bits=list(request.bits)
            )
        raise AssertionError(f"fake gateway cannot answer {request!r}")


def _load_device(fake: _FakeTcpClient) -> None:
    """Fill a fake gateway with a factory-like register image."""
    registers = fake.input_registers
    registers[0:10] = [ord(character) for character in "SCIETEX.RU"]
    registers[10:30] = [ord(character) for character in "EBIC_ADC".ljust(20)]
    registers[30:33] = [2, 3, 2]
    registers[InputRegister.SERIAL_HIGH] = 0x1234
    registers[InputRegister.SERIAL_LOW] = 0x5678
    registers[InputRegister.CHECK] = 7
    registers[InputRegister.RELAY_STATE] = 0
    registers[InputRegister.ADC_FLAGS] = int(AdcFlags.STATS_READY)
    registers[InputRegister.STATS_BLOCK_LOW] = 42
    registers[InputRegister.ADC_RATE_HIGH], registers[InputRegister.ADC_RATE_LOW] = (
        divmod(100_000, 0x10000)
    )
    registers[InputRegister.WINDOW_SIZE] = 256
    registers[InputRegister.ERROR_COUNT_LOW] = 1
    registers[InputRegister.PAUSE_COUNT_LOW] = 2
    registers[InputRegister.STATS_AGE_LOW] = 7
    stats = InputRegister.PA0_STATS_START
    registers[stats + 0] = 1200 * 16
    registers[stats + 1] = 1000
    registers[stats + 2] = 1400
    registers[stats + 3] = 1200
    registers[stats + 4] = 1000
    registers[stats + 5] = 100
    registers[stats + 6] = 400
    registers[stats + 7] = 2
    registers[InputRegister.CAPTURE_BLOCK_LOW] = 3
    registers[InputRegister.LAST_MEAN_PA0_MV] = 1500
    registers[InputRegister.LAST_MEAN_PA1_MV] = 900
    registers[InputRegister.OVERLOAD] = 1
    registers[InputRegister.SELECTED_CHANNEL] = 0
    registers[InputRegister.CAPTURE_CHANNEL] = 0
    registers[InputRegister.CAPTURE_AVERAGING] = 10
    registers[InputRegister.STOP_REASON] = int(StopReason.NONE)
    for index in range(CAPTURE_POINTS):
        registers[InputRegister.CAPTURE_START + 2 * index] = index
        registers[InputRegister.CAPTURE_START + 2 * index + 1] = 0

    fake.holding_registers[HoldingRegister.SLAVE_ADDRESS] = DEFAULT_SLAVE_ADDRESS
    fake.holding_registers[HoldingRegister.BAUD_INDEX] = 2
    fake.holding_registers[HoldingRegister.CHANNEL] = 0
    fake.holding_registers[HoldingRegister.AVERAGING] = 10
    fake.holding_registers[HoldingRegister.RATE_MODE] = 0


@pytest.fixture
def fake_tcp(monkeypatch):
    """Replace the pymodbus TCP client constructor for one test."""
    instances: list[_FakeTcpClient] = []

    class FakeTcpClient(_FakeTcpClient):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            instances.append(self)

    FakeTcpClient.instances = instances
    monkeypatch.setattr(client_tcp, "AsyncModbusTcpClient", FakeTcpClient)
    return FakeTcpClient


def _run_with(driver: EBICModbusV1BoosterTcp, scenario) -> None:
    """Run one scenario against ``driver`` inside a live event loop."""

    async def main() -> None:
        try:
            await scenario(driver)
        finally:
            await driver.close()

    asyncio.run(main())


def _run(scenario) -> None:
    """Run one scenario against a driver with default constructor arguments."""
    _run_with(EBICModbusV1BoosterTcp("gateway.local"), scenario)


def _write_register(address: int = 4, value: int = 1) -> WriteSingleRegisterRequest:
    """A single-register write a caller could hand to ``execute`` directly."""
    return WriteSingleRegisterRequest(address=address, registers=[value])


# -- construction and tuning ----------------------------------------------


def test_constructor_does_not_create_a_client(fake_tcp):
    """The client binds to the loop, so it is built only inside connect()."""
    driver = EBICModbusV1BoosterTcp("gateway.local")

    assert fake_tcp.instances == []
    assert driver._client is None
    assert (driver._host, driver._port) == ("gateway.local", 502)
    assert driver.address == DEFAULT_SLAVE_ADDRESS
    assert driver.label == "EBIC Boost"
    assert driver.timeout == 6.0
    assert driver._retries == 0
    assert driver._read_attempts == READ_ATTEMPTS


def test_constructor_keeps_the_injected_parameters():
    """Every constructor argument reaches the attribute it configures."""
    logger = getLogger("test.tcp")
    tuning = BoosterTuning(divider_ratio=0.5)

    driver = EBICModbusV1BoosterTcp(
        "gateway.local",
        port=1502,
        address=7,
        label="Lab",
        tuning=tuning,
        timeout=2.5,
        retries=2,
        logger=logger,
    )

    assert (driver._host, driver._port) == ("gateway.local", 1502)
    assert driver.address == 7
    assert driver.label == "Lab"
    assert driver.logger is logger
    assert driver.timeout == 2.5
    assert driver._retries == 2
    assert driver.divider_ratio == 0.5


def test_tuning_carries_the_divider_and_attempts_but_not_the_gap():
    """A TCP gateway frames its own requests, so the RTU gap is dropped."""
    tuning = BoosterTuning(divider_ratio=0.5, transaction_gap_s=0.05, read_attempts=5)

    driver = EBICModbusV1BoosterTcp("gateway.local", tuning=tuning)

    assert driver._transaction_gap_s == 0
    assert driver._read_attempts == 5
    assert driver.divider_ratio == 0.5


def test_capabilities_are_inherited_unchanged():
    """The transport changes how a request reaches the board, not its surface."""
    assert EBICModbusV1BoosterTcp.capabilities is EBICModbusV1Booster.capabilities


def test_device_layer_is_inherited_not_the_protocol_default():
    """A missing device layer must fail loudly instead of reading as a driver."""
    driver = EBICModbusV1BoosterTcp("gateway.local")

    assert type(driver).read_data is not VoltageAmplifierProtocol.read_data
    assert type(driver).read_data is EBICModbusV1Booster.read_data


def test_label_setter_does_not_need_a_serial_client(fake_tcp):
    """The inherited setter rebuilds a serial client; this one only stores."""
    driver = EBICModbusV1BoosterTcp("gateway.local", label="")

    driver.label = "Lab"

    assert driver.label == "Lab"
    assert fake_tcp.instances == []


def test_con_params_is_rejected_explicitly():
    """There is no serial port, so the serial configuration has no value."""
    driver = EBICModbusV1BoosterTcp("gateway.local")

    with pytest.raises(NotImplementedError, match="no serial port"):
        _ = driver.con_params


# -- device layer over TCP -------------------------------------------------


def test_read_data_returns_the_decoded_snapshot(fake_tcp):
    """The polled snapshot decodes the status block through the divider."""

    async def scenario(driver) -> None:
        state = await driver.read_data()

        assert state.output_voltage_v == pytest.approx(1.5 / DEFAULT_DIVIDER_RATIO)
        assert state.output_overload is True
        assert state.inverted is True
        assert state.input_voltage_v is None
        assert state.output_current_a is None
        assert state.gain is None
        assert state.filters == ()

    _run(scenario)


def test_read_data_uses_the_selected_channel_mean(fake_tcp):
    """Channel 1 reads its own last-mean register, not channel 0's."""

    async def scenario(driver) -> None:
        await driver.connect()
        fake_tcp.instances[0].input_registers[InputRegister.SELECTED_CHANNEL] = 1

        state = await driver.read_data()

        assert state.output_voltage_v == pytest.approx(0.9 / DEFAULT_DIVIDER_RATIO)

    _run(scenario)


def test_read_data_reports_an_unavailable_mean_as_none(fake_tcp):
    """An unavailable mean is unknown, not 0 V, and the relay is read too."""

    async def scenario(driver) -> None:
        await driver.connect()
        registers = fake_tcp.instances[0].input_registers
        registers[InputRegister.LAST_MEAN_PA0_MV] = SENTINEL_UNAVAILABLE
        registers[InputRegister.RELAY_STATE] = 1

        state = await driver.read_data()

        assert state.output_voltage_v is None
        assert state.inverted is False

    _run(scenario)


def test_get_statistics_decodes_the_window(fake_tcp):
    """The statistics window is decoded from the selected channel's block."""

    async def scenario(driver) -> None:
        statistics = await driver.get_statistics()

        assert statistics.channel == 0
        assert statistics.window_samples == 256
        assert statistics.mean_v == pytest.approx(_physical(1200))
        assert statistics.min_v == pytest.approx(_physical(1000))
        assert statistics.max_v == pytest.approx(_physical(1400))
        assert statistics.rms_v == pytest.approx(_millivolts(1000))
        assert statistics.ac_rms_v == pytest.approx(_millivolts(100))
        assert statistics.peak_to_peak_v == pytest.approx(_millivolts(400))
        assert statistics.rail_samples == 2

    _run(scenario)


def test_capture_runs_end_to_end(fake_tcp):
    """Trigger, poll, metadata and the nine block reads produce the waveform."""

    async def scenario(driver) -> None:
        capture = await driver.capture()

        assert capture.channel == 0
        assert len(capture.points) == CAPTURE_POINTS
        assert capture.points[0] == 0.0
        assert capture.points[1] == pytest.approx(_physical(1))
        assert capture.averaging == 10
        assert capture.sample_rate_hz == 100_000
        assert capture.block_number == 3

        requests = [request for _, request in fake_tcp.instances[0].requests]
        writes = [
            request
            for request in requests
            if isinstance(request, WriteSingleRegisterRequest)
        ]
        assert [(write.address, write.registers) for write in writes] == [
            (HoldingRegister.CAPTURE_COMMAND, [1])
        ]
        blocks = [
            request
            for request in requests
            if isinstance(request, ReadInputRegistersRequest)
            and request.address >= InputRegister.CAPTURE_START
        ]
        assert blocks[0].address == InputRegister.CAPTURE_START
        assert sum(block.count for block in blocks) == _CAPTURE_REGISTERS

    _run(scenario)


def test_trigger_and_read_capture_metadata(fake_tcp):
    """The trigger is a single write; the metadata is decoded separately."""

    async def scenario(driver) -> None:
        await driver.trigger_capture()
        metadata = await driver.read_capture_metadata()

        assert metadata.block_number == 3
        assert metadata.channel == 0
        assert metadata.averaging == 10
        assert metadata.stop_reason is StopReason.NONE
        assert (metadata.adc_error, metadata.dma_error) == (0, 0)

        trigger = fake_tcp.instances[0].requests[0][1]
        assert isinstance(trigger, WriteSingleRegisterRequest)
        assert (trigger.address, trigger.registers) == (
            HoldingRegister.CAPTURE_COMMAND,
            [1],
        )

    _run(scenario)


def test_get_inverted_and_set_inverted_drive_the_relay(fake_tcp):
    """Inversion is the inverse of the relay coil, read and written with FC01/05."""

    async def scenario(driver) -> None:
        await driver.connect()
        client = fake_tcp.instances[0]
        client.coils[RELAY_COIL] = True

        assert await driver.get_inverted() is False

        await driver.set_inverted(True)

        assert client.coils[RELAY_COIL] is False
        assert await driver.get_inverted() is True

        write = client.requests[1][1]
        assert isinstance(write, WriteSingleCoilRequest)
        assert (write.address, write.bits) == (RELAY_COIL, [False])

    _run(scenario)


def test_read_identity_decodes_the_identity_block(fake_tcp):
    """The identity block is read over TCP exactly as over serial."""

    async def scenario(driver) -> None:
        identity = await driver.read_identity()

        assert (identity.vendor, identity.product) == ("SCIETEX.RU", "EBIC_ADC")
        assert (
            identity.version_major,
            identity.version_minor,
            identity.version_patch,
        ) == (2, 3, 2)
        assert identity.serial_number == 0x12345678

    _run(scenario)


def test_read_status_decodes_the_status_block(fake_tcp):
    """Flags, block numbers and counters survive the TCP transport."""

    async def scenario(driver) -> None:
        status = await driver.read_status()

        assert status.flags == AdcFlags.STATS_READY
        assert status.stats_block == 42
        assert status.adc_rate_hz == 100_000
        assert status.window_samples == 256
        assert status.error_count == 1
        assert status.pause_count == 2
        assert status.stats_age_ms == 7

    _run(scenario)


def test_read_acquisition_and_communication_settings(fake_tcp):
    """The persisted and configured settings are decoded over TCP."""

    async def scenario(driver) -> None:
        acquisition = await driver.read_acquisition_settings()
        communication = await driver.read_communication_settings()

        assert (acquisition.channel, acquisition.averaging) == (0, 10)
        assert (acquisition.rate_mode, acquisition.sample_rate_hz) == (0, 100_000)
        assert communication.slave_address == DEFAULT_SLAVE_ADDRESS
        assert (communication.baud_index, communication.baudrate) == (2, 19200)

    _run(scenario)


def test_acquisition_setters_write_their_registers(fake_tcp):
    """Channel, averaging and rate mode reach the holding registers."""

    async def scenario(driver) -> None:
        await driver.select_channel(1)
        await driver.set_averaging(64)
        await driver.set_rate_mode(2)

        holding = fake_tcp.instances[0].holding_registers
        assert holding[HoldingRegister.CHANNEL] == 1
        assert holding[HoldingRegister.AVERAGING] == 64
        assert holding[HoldingRegister.RATE_MODE] == 2

    _run(scenario)


# -- connection lifecycle --------------------------------------------------


def test_connect_forwards_the_client_arguments(fake_tcp):
    """connect() constructs the client with the configured parameters."""
    driver = EBICModbusV1BoosterTcp(
        "gateway.local", port=1502, address=7, timeout=2.5, retries=2
    )

    async def scenario(driver) -> None:
        assert fake_tcp.instances == []

        await driver.connect()
        client = fake_tcp.instances[0]
        assert (client.host, client.port) == ("gateway.local", 1502)
        assert (client.timeout, client.retries) == (2.5, 2)

        await driver.write_holding(4, 1)
        request = client.requests[0][1]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.dev_id == 7

    _run_with(driver, scenario)


def test_execute_connects_on_first_request(fake_tcp):
    """The request layer opens the transport itself."""

    async def scenario(driver) -> None:
        assert fake_tcp.instances == []

        await driver.write_holding(4, 1)

        assert len(fake_tcp.instances) == 1
        assert fake_tcp.instances[0].connect_calls == 1

    _run(scenario)


def test_connect_is_idempotent(fake_tcp):
    """A second connect reuses the live transport instead of replacing it."""

    async def scenario(driver) -> None:
        await driver.connect()
        await driver.connect()

        assert len(fake_tcp.instances) == 1
        assert fake_tcp.instances[0].connect_calls == 1

    _run(scenario)


def test_execute_reconnects_a_dropped_link(fake_tcp):
    """A client that reports itself disconnected is reconnected in place."""

    async def scenario(driver) -> None:
        await driver.connect()
        client = fake_tcp.instances[0]
        client.connected = False

        await driver.write_holding(4, 1)

        assert client.connect_calls == 2
        assert len(fake_tcp.instances) == 1

    _run(scenario)


def test_close_is_idempotent_and_the_next_connect_rebuilds(fake_tcp):
    """close() releases the transport; repeating it is a no-op."""

    async def scenario(driver) -> None:
        await driver.connect()
        first = fake_tcp.instances[0]

        await driver.close()
        assert first.closed is True
        assert driver._client is None

        await driver.close()
        assert driver._client is None

        await driver.connect()
        assert len(fake_tcp.instances) == 2
        assert fake_tcp.instances[1] is not first

    _run(scenario)


def test_connect_failure_raises_modbus_operation_error(fake_tcp):
    """pymodbus reports a refused connection as False, which must become an error."""
    fake_tcp.default_connect_result = False

    async def scenario(driver) -> None:
        with pytest.raises(ModbusOperationError, match="failed to connect"):
            await driver.connect()

    _run(scenario)


def test_connect_failure_raises_from_a_request(fake_tcp):
    """A failed lazy connect surfaces as the shared operation error."""
    fake_tcp.default_connect_result = False

    async def scenario(driver) -> None:
        with pytest.raises(ModbusOperationError, match="failed to connect"):
            await driver.read_data()

    _run(scenario)


def test_connect_failure_is_swallowed_when_errors_are_disabled(fake_tcp):
    """A failed lazy connect returns None when raise_on_error is False."""
    fake_tcp.default_connect_result = False

    async def scenario(driver) -> None:
        assert await driver.execute(_write_register(), raise_on_error=False) is None

    _run(scenario)


# -- unit id on the wire ---------------------------------------------------


def test_execute_stamps_the_configured_unit_id(fake_tcp):
    """execute() addresses a caller-built request that carries no unit id."""
    driver = EBICModbusV1BoosterTcp("gateway.local", address=7)
    request = _write_register()
    # pymodbus defaults dev_id to 0, so this asserts the stamp, not the builder.
    assert request.dev_id == 0

    async def scenario(driver) -> None:
        await driver.execute(request)

        forwarded = fake_tcp.instances[0].requests[0][1]
        assert forwarded is request
        assert forwarded.dev_id == 7

    _run_with(driver, scenario)


# -- error mapping ---------------------------------------------------------


def test_exception_code_eleven_raises_gateway_target_failed(fake_tcp):
    """Code 11 means the gateway reached the bus but not the device."""

    async def scenario(driver) -> None:
        await driver.connect()
        fake_tcp.instances[0].override = ExceptionResponse(0x06, exception_code=0x0B)

        with pytest.raises(GatewayTargetFailed) as excinfo:
            await driver.read_data()

        assert isinstance(excinfo.value, ModbusOperationError)
        assert "exception code 11" in str(excinfo.value)

    _run_with(
        EBICModbusV1BoosterTcp("gateway.local", tuning=BoosterTuning(read_attempts=1)),
        scenario,
    )


def test_exception_code_six_raises_gateway_busy(fake_tcp):
    """Code 6 is a retryable contention signal, not a dead board."""

    async def scenario(driver) -> None:
        await driver.connect()
        fake_tcp.instances[0].override = ExceptionResponse(0x06, exception_code=0x06)

        with pytest.raises(GatewayBusy) as excinfo:
            await driver.read_data()

        assert isinstance(excinfo.value, ModbusOperationError)
        assert not isinstance(excinfo.value, GatewayTargetFailed)
        assert "exception code 6" in str(excinfo.value)

    _run_with(
        EBICModbusV1BoosterTcp("gateway.local", tuning=BoosterTuning(read_attempts=1)),
        scenario,
    )


def test_other_exception_codes_raise_a_plain_operation_error(fake_tcp):
    """A rejected request is not a gateway target failure."""

    async def scenario(driver) -> None:
        await driver.connect()
        fake_tcp.instances[0].override = ExceptionResponse(0x06, exception_code=0x02)

        with pytest.raises(ModbusOperationError) as excinfo:
            await driver.read_data()

        assert not isinstance(excinfo.value, GatewayTargetFailed)
        assert not isinstance(excinfo.value, GatewayBusy)
        assert "exception code 2" in str(excinfo.value)

    _run_with(
        EBICModbusV1BoosterTcp("gateway.local", tuning=BoosterTuning(read_attempts=1)),
        scenario,
    )


def test_gateway_busy_is_distinct_from_gateway_target_failed():
    """A busy gateway must not be classified as an unreachable board."""
    assert issubclass(GatewayBusy, ModbusOperationError)
    assert not issubclass(GatewayBusy, GatewayTargetFailed)
    assert not issubclass(GatewayTargetFailed, GatewayBusy)


@pytest.mark.parametrize(
    "error",
    [ConnectionException("link lost"), ModbusIOException("no response")],
)
def test_pymodbus_transport_errors_raise_modbus_operation_error(fake_tcp, error):
    """Both transport failure classes map onto the shared error."""

    async def scenario(driver) -> None:
        await driver.connect()
        fake_tcp.instances[0].error = error

        with pytest.raises(ModbusOperationError) as excinfo:
            await driver.read_data()

        assert not isinstance(excinfo.value, GatewayTargetFailed)
        assert str(error) in str(excinfo.value)

    _run_with(
        EBICModbusV1BoosterTcp("gateway.local", tuning=BoosterTuning(read_attempts=1)),
        scenario,
    )


@pytest.mark.parametrize(
    "outcome",
    [
        ExceptionResponse(0x06, exception_code=0x0B),
        ExceptionResponse(0x06, exception_code=0x06),
        ExceptionResponse(0x06, exception_code=0x02),
        ConnectionException("link lost"),
        ModbusIOException("no response"),
    ],
)
def test_raise_on_error_false_returns_none(fake_tcp, outcome):
    """Every error path honours the serial client's error-swallowing flag."""

    async def scenario(driver) -> None:
        await driver.connect()
        client = fake_tcp.instances[0]
        if isinstance(outcome, Exception):
            client.error = outcome
        else:
            client.override = outcome

        assert await driver.execute(_write_register(), raise_on_error=False) is None

    _run(scenario)


def test_no_response_expected_returns_none_without_reading(fake_tcp):
    """The gateway is told not to reply, so no response is parsed."""

    async def scenario(driver) -> None:
        await driver.connect()
        client = fake_tcp.instances[0]
        request = _write_register()

        assert await driver.execute(request, no_response_expected=True) is None
        assert client.requests == [(True, request)]
        assert client.holding_registers[4] == 1

    _run(scenario)


# -- serial-only members ---------------------------------------------------


def test_set_baud_index_raises_not_implemented(fake_tcp):
    """The gateway owns the RS485 wire speed, so there is nothing to set here."""

    async def scenario(driver) -> None:
        with pytest.raises(NotImplementedError, match="gateway owns"):
            await driver.set_baud_index(3)
        assert fake_tcp.instances == []

    _run(scenario)


def test_set_slave_address_writes_the_register_and_adopts_it(fake_tcp, monkeypatch):
    """The device address is persisted like on serial, then used for requests."""
    monkeypatch.setattr(client_module, "REBOOT_DELAY_S", 0.0)

    async def scenario(driver) -> None:
        await driver.set_slave_address(9)

        request = fake_tcp.instances[0].requests[0][1]
        assert isinstance(request, WriteSingleRegisterRequest)
        assert request.address == HoldingRegister.SLAVE_ADDRESS
        assert request.registers == [9]
        assert request.dev_id == DEFAULT_SLAVE_ADDRESS
        assert driver.address == 9

        await driver.write_holding(4, 1)
        assert fake_tcp.instances[0].requests[1][1].dev_id == 9

    _run(scenario)


@pytest.mark.parametrize("address", [0, 248])
def test_set_slave_address_rejects_an_out_of_range_address(fake_tcp, address):
    """The Modbus address space is enforced before any traffic."""

    async def scenario(driver) -> None:
        with pytest.raises(ValueError, match="slave address must be"):
            await driver.set_slave_address(address)
        assert fake_tcp.instances == []

    _run(scenario)


def test_set_slave_address_requires_the_eeprom_timeout(fake_tcp):
    """A write the device may persist after the timeout is rejected up front."""
    driver = EBICModbusV1BoosterTcp("gateway.local", timeout=1.0)

    async def scenario(driver) -> None:
        with pytest.raises(ValueError, match=f"at least {EEPROM_WRITE_TIMEOUT_S}"):
            await driver.set_slave_address(9)
        assert fake_tcp.instances == []

    _run_with(driver, scenario)


# -- entry points ----------------------------------------------------------


def test_tcp_entry_point_resolves():
    """The TCP driver is advertised under the driver entry point group."""
    driver = resolve_driver("ebic_modbus_tcp_v1_boost")

    # The registry loads the installed package, a separate module object from
    # the ``src.``-prefixed one the rest of the suite imports.
    assert driver.__name__ == "EBICModbusV1BoosterTcp"
    assert driver.__module__.endswith("voltage_amplifier.ebic.modbus.v1.client_tcp")
    assert driver.read_data is not VoltageAmplifierProtocol.read_data
