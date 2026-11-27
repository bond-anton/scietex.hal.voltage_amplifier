"""Tests for the EBIC_ADC transport-independent request layer.

A stub execute coroutine records every request and replays canned responses, so
the tests cover frame construction, echo validation, read retries and the
single-attempt write policy without opening a port. The transaction gap is
disabled so the retry timing is the only delay.
"""

import asyncio

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
from scietex.hal.serial import ModbusOperationError

try:
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.requests import (
        BoosterRequests,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.requests import (
        BoosterRequests,
    )

_SLAVE_ADDRESS = 2


class StubRequests(BoosterRequests):
    """Request layer whose transport replays canned responses."""

    def __init__(self, outcomes: list[ModbusPDU | Exception]) -> None:
        self.address = _SLAVE_ADDRESS
        self.sent: list[ModbusPDU] = []
        self._outcomes = list(outcomes)
        super().__init__(
            self._stub_execute,
            transaction_gap_s=0.0,
        )

    async def _stub_execute(self, request: ModbusPDU) -> ModbusPDU | None:
        self.sent.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_read_inputs_builds_an_fc04_request():
    """The range and the configured address reach the wire unchanged."""
    stub = StubRequests([ReadInputRegistersResponse(registers=[10, 20])])

    values = asyncio.run(stub.read_inputs(52, 2))

    assert values == [10, 20]
    request = stub.sent[0]
    assert isinstance(request, ReadInputRegistersRequest)
    assert request.address == 52
    assert request.count == 2
    assert request.dev_id == _SLAVE_ADDRESS


def test_read_holding_builds_an_fc03_request():
    """Holding reads use FC03 and validate the returned count."""
    stub = StubRequests([ReadHoldingRegistersResponse(registers=[7])])

    values = asyncio.run(stub.read_holding(4, 1))

    assert values == [7]
    request = stub.sent[0]
    assert isinstance(request, ReadHoldingRegistersRequest)
    assert request.address == 4
    assert request.count == 1
    assert request.dev_id == _SLAVE_ADDRESS


def test_write_holding_builds_an_fc06_request():
    """A single-register write carries the value and checks its echo."""
    stub = StubRequests([WriteSingleRegisterResponse(address=4, registers=[1000])])

    asyncio.run(stub.write_holding(4, 1000))

    request = stub.sent[0]
    assert isinstance(request, WriteSingleRegisterRequest)
    assert request.address == 4
    assert request.registers == [1000]
    assert request.dev_id == _SLAVE_ADDRESS


@pytest.mark.parametrize(
    ("closed", "encoded"),
    [(False, b"\x00\x00"), (True, b"\xff\x00")],
)
def test_write_relay_encodes_the_coil_value(closed, encoded):
    """FC05 encodes open as 0x0000 and closed as 0xFF00."""
    stub = StubRequests([WriteSingleCoilResponse(address=0, bits=[closed])])

    asyncio.run(stub.write_relay(closed))

    request = stub.sent[0]
    assert isinstance(request, WriteSingleCoilRequest)
    assert request.bits == [closed]
    assert request.dev_id == _SLAVE_ADDRESS
    assert request.encode()[2:4] == encoded


@pytest.mark.parametrize(("bit", "expected"), [(True, True), (False, False)])
def test_read_relay_reads_coil_zero(bit, expected):
    """Coil 0 is read with FC01; a closed coil means no inversion."""
    stub = StubRequests([ReadCoilsResponse(bits=[bit])])

    closed = asyncio.run(stub.read_relay())

    assert closed is expected
    request = stub.sent[0]
    assert isinstance(request, ReadCoilsRequest)
    assert request.address == 0
    assert request.count == 1
    assert request.dev_id == _SLAVE_ADDRESS


def test_read_rejects_a_response_of_the_wrong_pdu_type():
    """A write response cannot answer a read."""
    stub = StubRequests([WriteSingleRegisterResponse(address=0, registers=[1])])

    with pytest.raises(UnexpectedResponse):
        asyncio.run(stub.read_inputs(0, 1))


def test_read_rejects_a_short_register_list():
    """The response must carry exactly the requested number of registers."""
    stub = StubRequests([ReadInputRegistersResponse(registers=[1])])

    with pytest.raises(UnexpectedResponse):
        asyncio.run(stub.read_inputs(0, 2))


def test_holding_write_rejects_a_mismatched_echo():
    """A write acknowledged with a different value did not take effect."""
    stub = StubRequests([WriteSingleRegisterResponse(address=4, registers=[999])])

    with pytest.raises(UnexpectedResponse):
        asyncio.run(stub.write_holding(4, 1000))


def test_relay_write_rejects_a_mismatched_echo():
    """A relay write must echo the coil state it wrote."""
    stub = StubRequests([WriteSingleCoilResponse(address=0, bits=[False])])

    with pytest.raises(UnexpectedResponse):
        asyncio.run(stub.write_relay(True))


def test_read_is_retried_three_times_before_the_error_propagates():
    """A transient transport failure gets three total attempts."""
    stub = StubRequests([ModbusOperationError("bus") for _ in range(3)])

    with pytest.raises(ModbusOperationError):
        asyncio.run(stub.read_inputs(0, 1))

    assert len(stub.sent) == 3


def test_read_returns_the_first_successful_attempt():
    """A retry stops as soon as the transport answers."""
    stub = StubRequests(
        [
            ModbusOperationError("bus"),
            ReadInputRegistersResponse(registers=[7]),
        ]
    )

    values = asyncio.run(stub.read_inputs(0, 1))

    assert values == [7]
    assert len(stub.sent) == 2


def test_write_is_attempted_exactly_once_on_transport_failure():
    """A timed-out write is never replayed."""
    stub = StubRequests([ModbusOperationError("bus")])

    with pytest.raises(ModbusOperationError):
        asyncio.run(stub.write_holding(0, 1))

    assert len(stub.sent) == 1


def test_register_range_beyond_one_read_is_rejected():
    """One read may ask for at most 61 registers."""
    stub = StubRequests([])

    with pytest.raises(ValueError, match="count"):
        asyncio.run(stub.read_inputs(0, 62))

    assert stub.sent == []
