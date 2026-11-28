"""Modbus/TCP driver for the EBIC_ADC booster.

The board speaks Modbus RTU over RS485; this module reaches the same registers
through a Modbus/TCP gateway that translates the requests. The device layer is
inherited from :class:`~.client.EBICModbusV1Booster`: the state snapshot,
statistics, capture, identity, settings and the inversion relay reach the board
only through the request layer, so replacing the transport is enough to run all
of them over a gateway. Only the transport is rewritten here.

The serial machinery of the base class is deliberately never constructed.
``RS485Client.__init__`` is not called, because a gateway endpoint replaces the
serial port, and the base class constructor would build a ``pymodbus`` serial
client for it. The members that would reach that absent client are neutralised
rather than left to fail with an ``AttributeError``:

* ``con_params`` raises ``NotImplementedError``; the endpoint, timeout and
  retries are constructor arguments instead.
* the label setter only stores the label, because the inherited setter rebuilds
  the serial client;
* ``set_baud_index`` raises ``NotImplementedError``: the gateway owns the RS485
  wire speed, so there is no local baud rate to change. ``set_slave_address``
  still works, because the address belongs to the device, not to the link; a
  gateway with a fixed device map must be reconfigured to the new address.

The connection is opened lazily, on the first
:meth:`EBICModbusV1BoosterTcp.execute` or on an explicit
:meth:`EBICModbusV1BoosterTcp.connect`, because ``AsyncModbusTcpClient`` binds to
the running event loop in its constructor. A dropped link is reopened before the
next request.

The t3.5 transaction gap belongs to the RS485 bus: a Modbus/TCP gateway frames
requests itself, so the gap is forced to zero and only the tuning's divider and
read-attempt budget are carried over.

Exception responses from the gateway are mapped onto the exception hierarchy the
serial path already uses, with two named gateway outcomes: code 11 (the gateway
could not reach the device) becomes
:class:`~.exceptions.GatewayTargetFailed`, and code 6 (server device busy)
becomes :class:`~.exceptions.GatewayBusy`.

Classes:
    EBICModbusV1BoosterTcp: The EBIC_ADC booster over Modbus/TCP.
"""

import asyncio
from logging import Logger, getLogger
from typing import NoReturn

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse, ModbusPDU
from scietex.hal.serial import ModbusOperationError

from .client import BoosterTuning, EBICModbusV1Booster
from .constants import DEFAULT_SLAVE_ADDRESS, EEPROM_WRITE_TIMEOUT_S
from .exceptions import (
    GATEWAY_BUSY,
    GATEWAY_TARGET_FAILED,
    GatewayBusy,
    GatewayTargetFailed,
)
from .requests import BoosterRequests


class EBICModbusV1BoosterTcp(EBICModbusV1Booster):
    """Modbus/TCP driver for the EBIC_ADC booster behind a gateway.

    The device layer is inherited unchanged from the serial driver, so
    ``read_data``, ``get_statistics``, ``capture``, ``read_identity``,
    ``read_status``, the settings accessors and the inversion relay behave
    exactly as they do over RS485. What differs is the connection: one TCP
    client to the gateway, opened lazily and reopened after a drop.

    The serial side of the base class is never initialised. There is no serial
    port, so ``con_params`` is unavailable, and the inherited label setter is
    replaced because it would rebuild a serial client. The baud rate belongs to
    the gateway's RS485 segment, so ``set_baud_index`` rejects the change.

    Attributes:
        capabilities: Inherited from the serial driver: a unipolar output range
            set by the ADC reference and the divider, the output-overload flag,
            inversion, statistics and capture. The transport changes how a
            request reaches the board, not what the board can do.
    """

    def __init__(  # noqa: PLR0913, PLR0917 - the endpoint replaces con_params
        self,
        host: str,
        port: int = 502,
        address: int = DEFAULT_SLAVE_ADDRESS,
        label: str = "EBIC Boost",
        tuning: BoosterTuning | None = None,
        timeout: float = 6.0,
        retries: int = 0,
        logger: Logger | None = None,
    ) -> None:
        """
        Initialize the driver.

        No serial client is built and the base class constructor is not called:
        the gateway endpoint replaces the serial connection, and the request
        layer is wired to :meth:`execute` directly.

        Args:
            host: Gateway host name or address.
            port: Gateway TCP port.
            address: Modbus address the gateway routes to the booster.
            label: Human-readable label used in log and error messages.
            tuning: Divider, transaction gap and read attempts. Defaults to
                :class:`~.client.BoosterTuning`. The transaction gap is ignored
                over TCP and forced to zero.
            timeout: Per-request timeout in seconds.
            retries: Retries pymodbus performs per request. The default of zero
                keeps an ambiguous write from being sent twice.
            logger: Optional logger for transport diagnostics.
        """
        self.label = label
        self.address = address
        self.logger = logger if logger is not None else getLogger()
        self._host = host
        self._port = port
        self._timeout = timeout
        self._retries = retries
        self._tuning = tuning if tuning is not None else BoosterTuning()
        # The inherited device layer serialises whole operations with this
        # lock; the serial constructor that normally creates it is skipped.
        self._lock = asyncio.Lock()
        # `AsyncModbusTcpClient` binds to the running event loop in its
        # constructor, so it cannot be built here: the client is created in
        # `connect()`, which always runs inside the loop.
        self._client: AsyncModbusTcpClient | None = None
        BoosterRequests.__init__(
            self,
            self.execute,
            # The t3.5 gap separates RTU frames on a shared wire; a TCP gateway
            # frames its own requests, so pacing would only add latency.
            transaction_gap_s=0.0,
            read_attempts=self._tuning.read_attempts,
        )

    @property
    def label(self) -> str:
        """The label prefixed to log and error messages."""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        # The inherited setter rebuilds the serial client; this transport has
        # none, so the label is only stored.
        self._label = value

    @property
    def con_params(self) -> NoReturn:
        """
        Reject the serial connection configuration.

        The inherited property describes the serial port this transport does
        not have. It would raise ``AttributeError`` on the absent serial
        client, so it is replaced with an explicit statement.

        Raises:
            NotImplementedError: Always. The gateway endpoint replaces the
                serial port; ``timeout`` and ``retries`` are constructor
                arguments.
        """
        raise NotImplementedError(
            "con_params is unavailable over Modbus/TCP: this transport has no "
            "serial port. Pass the gateway endpoint, timeout and retries to "
            "the constructor instead."
        )

    @con_params.setter
    def con_params(self, params: object) -> None:
        """Reject assigning serial connection parameters; see the getter."""
        raise NotImplementedError(
            "con_params is unavailable over Modbus/TCP: this transport has no "
            "serial port. Pass the gateway endpoint, timeout and retries to "
            "the constructor instead."
        )

    @property
    def timeout(self) -> float:
        """Per-request TCP timeout in seconds."""
        return self._timeout

    async def connect(self) -> None:
        """
        Open the TCP connection to the gateway.

        Creating the client is lazy and the call is idempotent: an already
        connected client short-circuits, and pymodbus is never asked for a
        second transport on top of a live one.

        Raises:
            ModbusOperationError: If the gateway is unreachable. pymodbus
                reports a failed connect by returning ``False`` rather than
                raising, so the result is checked here; otherwise a caller would
                treat an unreachable gateway as connected and only fail later,
                on the first request.
        """
        if self._client is not None and self._client.connected:
            return
        self.logger.debug(
            "%sConnecting to Modbus/TCP gateway %s:%s.",
            self._prefix,
            self._host,
            self._port,
        )
        if self._client is None:
            self._client = AsyncModbusTcpClient(
                self._host,
                port=self._port,
                timeout=self.timeout,
                retries=self._retries,
            )
        if not await self._client.connect():
            raise ModbusOperationError(
                f"{self._prefix}failed to connect to Modbus/TCP gateway at "
                f"{self._host}:{self._port}"
            )

    async def close(self) -> None:
        """Close the TCP connection and drop the client; safe to repeat."""
        if self._client is not None:
            self._client.close()
            self._client = None

    async def execute(
        self,
        request: ModbusPDU,
        no_response_expected: bool = False,
        raise_on_error: bool = True,
    ) -> ModbusPDU | None:
        """
        Send one request to the gateway and return its response.

        The connection is opened or reopened first, so call sites never manage
        it. The response contract matches
        :meth:`scietex.hal.serial.RS485Client.execute`: ``None`` when no
        response is expected or when an error is swallowed, otherwise the PDU.

        Args:
            request: The request to send.
            no_response_expected: Send the request without waiting for a reply.
            raise_on_error: Raise on a transport failure or an exception
                response instead of returning ``None``. Pass ``False`` to keep
                the error-swallowing behaviour.

        Returns:
            ModbusPDU | None: The response, or ``None`` when none was read.

        Raises:
            GatewayBusy: If the gateway or the device reports that it is busy
                (exception code 6). The request may succeed shortly.
            GatewayTargetFailed: If the gateway reports that it could not reach
                the addressed device (exception code 11).
            ModbusOperationError: If the connection fails, pymodbus raises a
                connection or I/O error, or the device returns another
                exception response.
        """
        # The request layer stamps the address on every PDU it builds, but this
        # method is public: a caller-built request keeps pymodbus's default
        # (0) unless the transport attaches the configured address here.
        request.dev_id = self.address
        if self._client is None or not self._client.connected:
            try:
                await self.connect()
            except ModbusOperationError:
                if no_response_expected or not raise_on_error:
                    return None
                raise
        try:
            response = await self._require_client().execute(
                no_response_expected, request
            )
        except (ConnectionException, ModbusIOException) as exc:
            if no_response_expected or not raise_on_error:
                return None
            raise ModbusOperationError(
                f"{self._prefix}Modbus/TCP request to {self._host}:{self._port} "
                f"failed: {exc}"
            ) from exc
        if no_response_expected:
            return None
        if isinstance(response, ExceptionResponse):
            if not raise_on_error:
                return None
            if response.exception_code == GATEWAY_TARGET_FAILED:
                raise GatewayTargetFailed(
                    f"{self._prefix}gateway at {self._host}:{self._port} could "
                    f"not reach device {self.address}: exception code "
                    f"{response.exception_code}"
                )
            if response.exception_code == GATEWAY_BUSY:
                raise GatewayBusy(
                    f"{self._prefix}gateway at {self._host}:{self._port} "
                    f"reported device {self.address} busy: exception code "
                    f"{response.exception_code}"
                )
            raise ModbusOperationError(
                f"{self._prefix}device {self.address} returned exception code "
                f"{response.exception_code}"
            )
        return response

    async def set_baud_index(self, index: int) -> None:
        """
        Reject a baud-index change: the gateway owns the RS485 wire speed.

        The serial driver persists the index and rebuilds its port at the new
        rate. Over Modbus/TCP there is no local port, so changing the index here
        would either be a silent no-op or would misconfigure a link this class
        does not control. The gateway must be reconfigured instead.

        Args:
            index: Unused; accepted only to match the serial driver's signature.

        Raises:
            NotImplementedError: Always. The TCP transport has no wire speed to
                set.
        """
        raise NotImplementedError(
            "set_baud_index() is unavailable over Modbus/TCP: the gateway owns "
            "the RS485 link, so there is no local baud rate to change. "
            "Reconfigure the gateway's serial port instead."
        )

    def _require_eeprom_timeout(self) -> None:
        """Reject a transport timeout too short for an EEPROM write."""
        if self.timeout < EEPROM_WRITE_TIMEOUT_S:
            raise ValueError(
                f"EEPROM writes need a transport timeout of at least "
                f"{EEPROM_WRITE_TIMEOUT_S} s, got {self.timeout}"
            )

    def _require_client(self) -> AsyncModbusTcpClient:
        """
        Return the connected client.

        ``connect()`` leaves a client in place, so the guard only makes the
        invariant explicit for type checkers and for a future transport that
        stops constructing eagerly.

        Raises:
            RuntimeError: If no client exists.
        """
        if self._client is None:
            raise RuntimeError(
                "Modbus/TCP client is not connected; call connect() first"
            )
        return self._client

    @property
    def _prefix(self) -> str:
        """Return the label with its separator, or nothing when unlabeled."""
        return f"{self.label}: " if self.label else ""
