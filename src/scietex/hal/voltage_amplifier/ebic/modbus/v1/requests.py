"""Transport-independent request layer for the EBIC_ADC firmware.

:class:`BoosterRequests` builds Modbus requests and reaches the device only
through the ``execute`` callable its owner supplies. A serial transport wraps
:class:`scietex.hal.serial.RS485Client.execute`, and a sibling transport can
reuse this layer by wiring a different connection; nothing here imports a
transport.

Every request carries the owner's ``address``. Consecutive requests are separated
by the Modbus RTU t3.5 gap, because a frame sent too soon after the previous one
is not read as a new frame. Reads are retried on
:class:`scietex.hal.serial.ModbusOperationError`; writes are never retried, since
a write that timed out may still have reached the device and a blind replay
could apply it twice. A response that cannot belong to its request raises
:class:`UnexpectedResponse` and is never retried.

Classes:
    BoosterRequests: The request layer; a transport supplies ``execute``.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

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

from .constants import (
    DEFAULT_SLAVE_ADDRESS,
    MAX_REGISTERS_PER_READ,
    MIN_TRANSACTION_GAP_S,
    READ_ATTEMPTS,
    RELAY_COIL,
)
from .errors import UnexpectedResponse

Execute = Callable[[ModbusPDU], Awaitable[ModbusPDU | None]]
"""Send one request and return its response, or ``None`` when there is none."""

_RETRY_BACKOFF_S = 0.05
"""Base delay between read attempts; the attempt number scales it."""

_MAX_WORD = 0xFFFF


class BoosterRequests:
    """The transport-independent request layer of the EBIC_ADC driver.

    The owning transport provides the ``address`` attribute and an ``execute``
    coroutine; a subclass that inherits a transport's ``__init__`` must call
    :meth:`__init__` with that transport's ``execute`` bound method.

    Attributes:
        address: Modbus slave address carried by every request.
    """

    address: int = DEFAULT_SLAVE_ADDRESS

    def __init__(
        self,
        execute: Execute,
        *,
        transaction_gap_s: float = MIN_TRANSACTION_GAP_S,
        read_attempts: int = READ_ATTEMPTS,
    ) -> None:
        """
        Initialize the request layer.

        Args:
            execute: Coroutine that sends one request and returns its response.
            transaction_gap_s: Minimum time between two requests, in seconds. A
                value of zero or less disables pacing.
            read_attempts: Total attempts a read makes, at least one.

        Raises:
            ValueError: If ``read_attempts`` is below one.
        """
        if read_attempts < 1:
            raise ValueError(f"read_attempts must be at least 1, got {read_attempts}")
        self._execute = execute
        self._transaction_gap_s = transaction_gap_s
        self._read_attempts = read_attempts
        self._last_request_at: float | None = None

    # -- registers ---------------------------------------------------------

    async def read_inputs(self, start: int, count: int) -> list[int]:
        """
        Read input registers with FC04.

        Args:
            start: Address of the first register.
            count: Number of registers, 1-61.

        Returns:
            list[int]: The register words in address order.

        Raises:
            ValueError: If the range is outside the address space or longer than
                one read may ask for.
            UnexpectedResponse: If the response is not a register read of
                exactly ``count`` words.
        """
        _check_range(start, count)
        response = await self._read(
            ReadInputRegistersRequest(
                address=start,
                count=count,
                dev_id=self.address,
            )
        )
        return _expect_registers(response, count, _range_label("input", start, count))

    async def read_holding(self, start: int, count: int) -> list[int]:
        """
        Read holding registers with FC03.

        Args:
            start: Address of the first register.
            count: Number of registers, 1-61.

        Returns:
            list[int]: The register words in address order.

        Raises:
            ValueError: If the range is outside the address space or longer than
                one read may ask for.
            UnexpectedResponse: If the response is not a register read of
                exactly ``count`` words.
        """
        _check_range(start, count)
        response = await self._read(
            ReadHoldingRegistersRequest(
                address=start,
                count=count,
                dev_id=self.address,
            )
        )
        return _expect_registers(response, count, _range_label("holding", start, count))

    async def write_holding(self, register: int, value: int) -> None:
        """
        Write one holding register with FC06.

        The write is attempted once: a transport failure may hide a write that
        already reached the device, so a replay is unsafe.

        Args:
            register: Register address.
            value: 16-bit value to write.

        Raises:
            ValueError: If the address or the value is outside 16 bits, before
                any bus traffic.
            UnexpectedResponse: If the device does not echo the write.
            ModbusOperationError: If the transport fails.
        """
        _check_word("register", register)
        _check_word("value", value)
        response = await self._execute_once(
            WriteSingleRegisterRequest(
                address=register,
                registers=[value],
                dev_id=self.address,
            )
        )
        if (
            not isinstance(response, WriteSingleRegisterResponse)
            or response.address != register
            or response.registers != [value]
        ):
            raise UnexpectedResponse(
                f"holding register {register}",
                f"expected echo of {value}, got {response!r}",
            )

    # -- relay -------------------------------------------------------------

    async def read_relay(self) -> bool:
        """
        Read relay 0 with FC01.

        The firmware drives relay 0 as its inversion switch: logic 0 inverts the
        output and logic 1 passes it. A closed coil therefore means no inversion.

        Returns:
            bool: Whether the relay is closed, meaning the output is not inverted.

        Raises:
            UnexpectedResponse: If the response is not a coil read with a value.
        """
        response = await self._read(
            ReadCoilsRequest(
                address=RELAY_COIL,
                count=1,
                dev_id=self.address,
            )
        )
        if not isinstance(response, ReadCoilsResponse):
            raise UnexpectedResponse(
                "relay",
                f"expected a coil response, got {response!r}",
            )
        bits = response.bits or []
        if not bits:
            raise UnexpectedResponse("relay", f"no coil value in {response!r}")
        return bool(bits[0])

    async def write_relay(self, closed: bool) -> None:
        """
        Drive relay 0 with FC05.

        ``closed`` encodes as coil value ``0xFF00`` and ``False`` as ``0x0000``.
        The write is attempted once, like every other write.

        Args:
            closed: ``True`` to close the relay (no inversion), ``False`` to open
                it (inversion on).

        Raises:
            UnexpectedResponse: If the device does not echo the written coil.
            ModbusOperationError: If the transport fails.
        """
        response = await self._execute_once(
            WriteSingleCoilRequest(
                address=RELAY_COIL,
                bits=[closed],
                dev_id=self.address,
            )
        )
        if (
            not isinstance(response, WriteSingleCoilResponse)
            or response.address != RELAY_COIL
            or response.bits != [closed]
        ):
            raise UnexpectedResponse(
                "relay",
                f"expected echo of coil {RELAY_COIL} = {closed}, got {response!r}",
            )

    # -- transport helpers -------------------------------------------------

    async def _pace(self) -> None:
        """Wait out the t3.5 gap before the next request."""
        if self._transaction_gap_s <= 0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._transaction_gap_s - (now - self._last_request_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
                now = time.monotonic()
        self._last_request_at = now

    async def _execute_once(self, request: ModbusPDU) -> ModbusPDU | None:
        """Send one request without retrying it."""
        await self._pace()
        return await self._execute(request)

    async def _read(self, request: ModbusPDU) -> ModbusPDU | None:
        """Send a read, retrying transport failures up to the attempt limit."""
        for attempt in range(1, self._read_attempts + 1):
            await self._pace()
            try:
                return await self._execute(request)
            except ModbusOperationError:
                if attempt == self._read_attempts:
                    raise
                await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
        raise RuntimeError("read attempts exhausted without an outcome")


def _check_range(start: int, count: int) -> None:
    """Reject a register range a single request cannot address."""
    if not 0 <= start <= _MAX_WORD:
        raise ValueError(f"start must be 0-{_MAX_WORD}, got {start}")
    if not 1 <= count <= MAX_REGISTERS_PER_READ:
        raise ValueError(f"count must be 1-{MAX_REGISTERS_PER_READ}, got {count}")
    if start + count - 1 > _MAX_WORD:
        raise ValueError(f"range {start}+{count} runs past register {_MAX_WORD}")


def _check_word(name: str, value: int) -> None:
    """Reject a value a 16-bit register cannot hold."""
    if not 0 <= value <= _MAX_WORD:
        raise ValueError(f"{name} must be 0-{_MAX_WORD}, got {value}")


def _range_label(table: str, start: int, count: int) -> str:
    """Name a register range in an error message."""
    return f"{table} registers {start}-{start + count - 1}"


def _expect_registers(
    response: ModbusPDU | None,
    count: int,
    label: str,
) -> list[int]:
    """Return the registers of a read response, or raise on a mismatch."""
    if not isinstance(
        response, (ReadHoldingRegistersResponse, ReadInputRegistersResponse)
    ):
        raise UnexpectedResponse(
            label, f"expected {count} register(s), got {response!r}"
        )
    registers = list(response.registers or ())
    if len(registers) != count:
        raise UnexpectedResponse(
            label,
            f"expected {count} register(s), got {len(registers)}",
        )
    return registers
