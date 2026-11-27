"""Modbus RTU driver for the EBIC_ADC booster.

The firmware exposes the booster as a set of read-only input registers and a
handful of writable holding registers: the selected acquisition channel, the
averaging depth, the rate mode, the inversion relay and the persisted
communication settings. The analog quantities are reported as ADC codes and
millivolts, so this driver scales them through the external divider before they
reach a caller.

The transport is the shared :class:`RS485Client`, which opens the port once per
operation: ``connect`` has nothing to open and every accessor is a coroutine.
The request layer is :class:`BoosterRequests`; this class supplies its
transport, serialises whole high-level operations with one lock and decodes the
register words. All requests carry the address the instance was built with. A
slave-address or baud-index change is acknowledged at the old settings and
followed by a firmware restart after ``REBOOT_DELAY_S``; see the setters for the
consequences.

A capture is the one operation that spans many requests: it reads the acquisition
settings, triggers the device, polls until the buffer is ready, reads the 512
interleaved sample registers in nine frames and verifies that the metadata did not
change underneath it. All of that runs under the same lock, so a concurrent
accessor cannot slip a request into the sequence.

Classes:
    BoosterTuning: Transport and divider tuning applied to the request layer.
    EBICModbusV1Booster: The EBIC_ADC driver over RS485/RTU.
"""

import asyncio
import time
from dataclasses import dataclass
from logging import Logger
from typing import ClassVar

from scietex.hal.serial import (
    ModbusSerialConnectionConfig,
    RS485Client,
    SerialConnectionConfig,
)

from ....base import (
    Access,
    AmplifierState,
    Capture,
    ChannelStatistics,
    IndicationCapabilities,
    MeasurementCapabilities,
    OutputVoltageCapabilities,
    VoltageAmplifierCapabilities,
    VoltageAmplifierProtocol,
)
from .constants import (
    ADC_REFERENCE_V,
    BAUD_RATES,
    CAPTURE_POINTS,
    CAPTURE_POLL_INTERVAL_S,
    CAPTURE_TIMEOUT_MARGIN_S,
    DEFAULT_DIVIDER_RATIO,
    DEFAULT_SLAVE_ADDRESS,
    EEPROM_WRITE_TIMEOUT_S,
    MAX_CAPTURE_AVERAGING,
    MIN_TRANSACTION_GAP_S,
    RATE_MODE_SAMPLE_RATES,
    READ_ATTEMPTS,
    REBOOT_DELAY_S,
    SENTINEL_UNAVAILABLE,
    SLAVE_ADDRESS_MAX,
    SLAVE_ADDRESS_MIN,
    HoldingRegister,
    InputRegister,
)
from .decode import (
    decode_capture_column,
    decode_channel_statistics,
    decode_stop_reason,
    u32,
)
from .errors import CaptureFailed, CaptureTimeout, UnexpectedResponse
from .identity import IDENTITY_REGISTER_COUNT, DeviceIdentity, decode_identity
from .requests import BoosterRequests
from .settings import (
    AcquisitionSettings,
    AdcFlags,
    CaptureMetadata,
    CommunicationSettings,
    DeviceStatus,
)

_MV_PER_V = 1000.0
"""Millivolts per volt, used to scale the last-mean registers."""

_STATUS_REGISTER_COUNT = InputRegister.ADC_VREF_MV + 1
"""Input registers 0-50: the status block plus the measured ADC reference."""

_CAPTURE_METADATA_START = InputRegister.CAPTURE_BLOCK_HIGH
"""First input register of the capture metadata block."""

_CAPTURE_METADATA_COUNT = InputRegister.HAL_DMA_ERROR - _CAPTURE_METADATA_START + 1
"""Input registers 72-82: block number, channel, averaging, stop reason, codes."""

_CAPTURE_CHUNK_REGISTERS = 60
"""Registers per capture read: whole channel pairs and under the frame limit."""

_CAPTURE_CHUNK_RANGES: tuple[tuple[int, int], ...] = tuple(
    (
        start,
        min(_CAPTURE_CHUNK_REGISTERS, InputRegister.CAPTURE_END + 1 - start),
    )
    for start in range(
        InputRegister.CAPTURE_START,
        InputRegister.CAPTURE_END + 1,
        _CAPTURE_CHUNK_REGISTERS,
    )
)
"""The nine reads covering input registers 128-639, 60 words each but the last."""


@dataclass(frozen=True, slots=True)
class BoosterTuning:
    """Transport and divider tuning for the EBIC_ADC driver.

    Attributes:
        divider_ratio: External input divider, 0.6 on the EBIC board. Physical
            volts are pin volts divided by it.
        transaction_gap_s: Minimum gap between two requests, in seconds. Zero
            disables pacing.
        read_attempts: Total attempts a read makes before its transport error
            propagates, at least one.
    """

    divider_ratio: float = DEFAULT_DIVIDER_RATIO
    transaction_gap_s: float = MIN_TRANSACTION_GAP_S
    read_attempts: int = READ_ATTEMPTS

    def __post_init__(self) -> None:
        """Reject a divider that cannot scale a voltage."""
        if self.divider_ratio <= 0:
            raise ValueError(
                f"divider_ratio must be positive, got {self.divider_ratio!r}"
            )


class EBICModbusV1Booster(RS485Client, BoosterRequests, VoltageAmplifierProtocol):
    """Modbus RTU client for the EBIC_ADC booster.

    Attributes:
        capabilities: The measurement and control surface the firmware exposes:
            a unipolar output range set by the ADC reference and the divider,
            the output-overload flag, inversion, statistics and capture.
    """

    capabilities: ClassVar[VoltageAmplifierCapabilities] = VoltageAmplifierCapabilities(
        output_voltage=OutputVoltageCapabilities(
            min_v=0.0,
            max_v=ADC_REFERENCE_V / DEFAULT_DIVIDER_RATIO,
        ),
        output_overload=IndicationCapabilities(read=True),
        invert_output=Access(read=True, write=True),
        measurement=MeasurementCapabilities(
            statistics=Access(read=True),
            capture=Access(read=True, write=True),
            max_capture_points=CAPTURE_POINTS,
            max_capture_averaging=MAX_CAPTURE_AVERAGING,
        ),
    )

    def __init__(  # noqa: PLR0913 - the constructor contract fixes these parameters
        self,
        con_params: SerialConnectionConfig | ModbusSerialConnectionConfig,
        address: int = DEFAULT_SLAVE_ADDRESS,
        label: str = "EBIC Boost",
        tuning: BoosterTuning | None = None,
        logger: Logger | None = None,
    ) -> None:
        """
        Initialize the client.

        Args:
            con_params: Serial port and framing configuration.
            address: Modbus slave address the device answers at.
            label: Human-readable label used in log messages.
            tuning: Divider, transaction gap and read attempts. Defaults to
                :class:`BoosterTuning`.
            logger: Optional logger for transport diagnostics.
        """
        self._tuning = tuning if tuning is not None else BoosterTuning()
        super().__init__(
            con_params=con_params,
            address=address,
            label=label,
            logger=logger,
        )
        BoosterRequests.__init__(
            self,
            self.execute,
            transaction_gap_s=self._tuning.transaction_gap_s,
            read_attempts=self._tuning.read_attempts,
        )
        self._lock = asyncio.Lock()

    @property
    def divider_ratio(self) -> float:
        """External input divider ratio, 0.6 on the EBIC board."""
        return self._tuning.divider_ratio

    async def connect(self) -> None:
        """Nothing to open: the serial client connects once per operation."""
        self.logger.debug("%s: Connect (per-operation serial client).", self.label)

    async def close(self) -> None:
        """Close the underlying Modbus client and release the serial port."""
        await super().close()

    # -- state -------------------------------------------------------------

    # `RS485Client.read_data` returns a raw register dictionary; the voltage
    # amplifier contract replaces it with a typed snapshot, so the override is
    # deliberate and the Liskov warning is expected.
    async def read_data(self) -> AmplifierState:  # ty: ignore[incompatible-method-override]
        """
        Read the output voltage, overload flag and inversion state.

        The status block spans input registers 0-49, while the last-mean and
        overload words sit above it, so two reads cover the snapshot: the first
        for the flag and relay state, the second for the measured values.

        Returns:
            AmplifierState: ``output_voltage_v`` is the last mean of the
            selected channel scaled through the divider, or ``None`` when no
            fresh statistics window exists or the mean is the unavailable
            sentinel. ``output_overload`` and ``inverted`` come from the device
            flag and relay state. Input voltage, current, gain and filters stay
            ``None``/empty because the firmware reports none of them.
        """
        async with self._lock:
            registers = await self._read_status_block()
            flags = AdcFlags(registers[InputRegister.ADC_FLAGS])
            selected = registers[InputRegister.SELECTED_CHANNEL]
            mean_register = (
                InputRegister.LAST_MEAN_PA1_MV
                if selected == 1
                else InputRegister.LAST_MEAN_PA0_MV
            )
            mean_mv = registers[mean_register]
            output_voltage_v = None
            if flags & AdcFlags.STATS_READY and mean_mv != SENTINEL_UNAVAILABLE:
                output_voltage_v = mean_mv / _MV_PER_V / self.divider_ratio
            return AmplifierState(
                output_voltage_v=output_voltage_v,
                output_overload=bool(registers[InputRegister.OVERLOAD]),
                inverted=registers[InputRegister.RELAY_STATE] == 0,
            )

    async def get_statistics(self) -> ChannelStatistics:
        """
        Read the statistics window of the channel currently measured.

        Returns:
            ChannelStatistics: Statistics decoded from the selected channel's
            block, with the window length the device reports. When no fresh
            window is ready every numeric field stays ``None``.
        """
        async with self._lock:
            registers = await self._read_status_block()
            flags = AdcFlags(registers[InputRegister.ADC_FLAGS])
            selected = registers[InputRegister.SELECTED_CHANNEL]
            window_samples = registers[InputRegister.WINDOW_SIZE]
            if not flags & AdcFlags.STATS_READY:
                return ChannelStatistics(
                    channel=selected,
                    window_samples=window_samples,
                )
            channel_stats_start = (
                InputRegister.PA0_STATS_START
                if selected == 0
                else InputRegister.PA1_STATS_START
            )
            return decode_channel_statistics(
                registers,
                channel=selected,
                channel_stats_start=channel_stats_start,
                divider_ratio=self.divider_ratio,
                window_samples=window_samples,
            )

    # -- capture -----------------------------------------------------------

    async def capture(self) -> Capture:
        """
        Trigger a capture, wait for it and read the buffered points.

        The whole sequence holds the lock: the settings read, the trigger, the
        completion polls, the metadata and the nine block reads are one
        operation, so no other accessor interleaves a request into it. The
        deadline is the nominal duration of the capture plus
        ``CAPTURE_TIMEOUT_MARGIN_S``.

        Returns:
            Capture: The 256 points of the captured channel in physical volts,
            with the averaging depth, sample rate and block number the device
            reported.

        Raises:
            CaptureFailed: If a previous capture is still pending, the settings
                name an averaging depth outside 1-4000, the device reports an ADC
                error, the metadata is incomplete, or it changes while the block
                is read.
            CaptureTimeout: If the buffer does not become ready before the
                deadline.
            UnexpectedResponse: If a response does not carry the requested
                range or the rate mode is not one the firmware defines.
        """
        async with self._lock:
            return await self._capture_locked()

    async def trigger_capture(self) -> None:
        """
        Start a capture without waiting for it to finish.

        The write is attempted once; a call while a capture is pending is left to
        the firmware to reject, because this helper reads no state first.

        Raises:
            UnexpectedResponse: If the device does not echo the write.
        """
        async with self._lock:
            await self._trigger_capture_locked()

    async def read_capture_metadata(self) -> CaptureMetadata:
        """
        Read what the last capture ran with and why it stopped.

        Returns:
            CaptureMetadata: The channel, averaging depth, block number, stop
            reason and HAL error codes. A field the device reports as
            unavailable, or a block number of zero, decodes to ``None``.

        Raises:
            UnexpectedResponse: If the stop reason is not one the firmware
                defines.
        """
        async with self._lock:
            return await self._read_capture_metadata_locked()

    async def _capture_locked(self) -> Capture:
        """Run one capture while the caller holds the lock."""
        settings = await self.read_holding(
            HoldingRegister.SLAVE_ADDRESS,
            HoldingRegister.RATE_MODE + 1,
        )
        if settings[HoldingRegister.CAPTURE_COMMAND] != 0:
            raise CaptureFailed("previous capture still pending")
        configured_averaging = settings[HoldingRegister.AVERAGING]
        if not 1 <= configured_averaging <= MAX_CAPTURE_AVERAGING:
            raise CaptureFailed(
                f"averaging {configured_averaging} is outside 1-{MAX_CAPTURE_AVERAGING}"
            )
        rate_mode = settings[HoldingRegister.RATE_MODE]
        sample_rate_hz = RATE_MODE_SAMPLE_RATES.get(rate_mode)
        if sample_rate_hz is None:
            raise UnexpectedResponse("rate mode", f"unknown value {rate_mode}")

        timeout_s = (
            CAPTURE_POINTS * configured_averaging / sample_rate_hz
            + CAPTURE_TIMEOUT_MARGIN_S
        )
        deadline = time.monotonic() + timeout_s

        await self._trigger_capture_locked()

        flags = AdcFlags(0)
        while True:
            status = await self.read_holding(
                HoldingRegister.SLAVE_ADDRESS,
                HoldingRegister.CAPTURE_COMMAND + 1,
            )
            (flag_word,) = await self.read_inputs(InputRegister.ADC_FLAGS, 1)
            flags = AdcFlags(flag_word)
            if flags & AdcFlags.ADC_ERROR:
                metadata = await self._read_capture_metadata_locked()
                raise CaptureFailed(_failure_detail("ADC error", metadata))
            if (
                flags & AdcFlags.CAPTURE_READY
                and status[HoldingRegister.CAPTURE_COMMAND] == 0
            ):
                break
            if time.monotonic() > deadline:
                # Refresh the metadata so a caller inspecting the device after
                # the timeout sees the block and stop reason of this attempt.
                await self._read_capture_metadata_locked()
                raise CaptureTimeout(timeout_s)
            await asyncio.sleep(CAPTURE_POLL_INTERVAL_S)

        metadata = await self._read_capture_metadata_locked()
        block_number, channel, averaging = _require_capture(metadata, flags)

        registers: list[int] = []
        for start, count in _CAPTURE_CHUNK_RANGES:
            registers.extend(await self.read_inputs(start, count))

        verified = await self._read_capture_metadata_locked()
        if (
            verified.block_number != block_number
            or verified.channel != channel
            or verified.averaging != averaging
        ):
            raise CaptureFailed("capture changed while reading")

        return Capture(
            channel=channel,
            points=decode_capture_column(registers, channel, self.divider_ratio),
            averaging=averaging,
            sample_rate_hz=sample_rate_hz,
            block_number=block_number,
        )

    async def _trigger_capture_locked(self) -> None:
        """Write the capture command while the caller holds the lock."""
        await self.write_holding(HoldingRegister.CAPTURE_COMMAND, 1)

    async def _read_capture_metadata_locked(self) -> CaptureMetadata:
        """Read input registers 72-82 while the caller holds the lock."""
        registers = await self.read_inputs(
            _CAPTURE_METADATA_START,
            _CAPTURE_METADATA_COUNT,
        )
        block_number = u32(registers[0], registers[1])
        channel = registers[InputRegister.CAPTURE_CHANNEL - _CAPTURE_METADATA_START]
        averaging = registers[InputRegister.CAPTURE_AVERAGING - _CAPTURE_METADATA_START]
        return CaptureMetadata(
            channel=None if channel == SENTINEL_UNAVAILABLE else channel,
            averaging=(None if averaging in (0, SENTINEL_UNAVAILABLE) else averaging),
            block_number=None if block_number == 0 else block_number,
            stop_reason=decode_stop_reason(
                registers[InputRegister.STOP_REASON - _CAPTURE_METADATA_START]
            ),
            adc_error=registers[InputRegister.HAL_ADC_ERROR - _CAPTURE_METADATA_START],
            dma_error=registers[InputRegister.HAL_DMA_ERROR - _CAPTURE_METADATA_START],
        )

    # -- inversion ---------------------------------------------------------

    async def get_inverted(self) -> bool:
        """
        Read the output-polarity inversion state.

        The inversion is the inverse of the relay coil: a closed coil passes the
        signal, an open one inverts it.

        Returns:
            bool: Whether the output is inverted.
        """
        async with self._lock:
            closed = await self.read_relay()
            return not closed

    async def set_inverted(self, inverted: bool) -> None:
        """
        Set the output-polarity inversion state.

        The inversion is the inverse of the relay coil: a closed coil passes the
        signal, an open one inverts it. The write is acknowledged by the device
        echoing the coil state, which is what :meth:`write_relay` checks.

        Args:
            inverted: Whether to invert the output.

        Raises:
            UnexpectedResponse: If the device does not echo the written coil.
        """
        async with self._lock:
            await self.write_relay(not inverted)

    # -- identity and status ----------------------------------------------

    async def read_identity(self) -> DeviceIdentity:
        """
        Read the vendor, product, version and serial number.

        One FC04 request reads input registers 0-35, the whole identity block.

        Returns:
            DeviceIdentity: The decoded identity.

        Raises:
            UnexpectedResponse: If the check register does not hold the
                identity constant.
        """
        async with self._lock:
            registers = await self.read_inputs(
                InputRegister.VENDOR_START,
                IDENTITY_REGISTER_COUNT,
            )
            return decode_identity(registers)

    async def read_status(self) -> DeviceStatus:
        """
        Read the device-side statistics status.

        Input registers 0-50 are read in one frame: the status fields occupy
        38-49 and register 50 carries the measured ADC reference.

        Returns:
            DeviceStatus: Flags, block number, ADC rate, window length, error and
            pause counters, and statistics age.
        """
        async with self._lock:
            registers = await self.read_inputs(
                InputRegister.VENDOR_START,
                _STATUS_REGISTER_COUNT,
            )
            return DeviceStatus(
                flags=AdcFlags(registers[InputRegister.ADC_FLAGS]),
                stats_block=u32(
                    registers[InputRegister.STATS_BLOCK_HIGH],
                    registers[InputRegister.STATS_BLOCK_LOW],
                ),
                adc_rate_hz=u32(
                    registers[InputRegister.ADC_RATE_HIGH],
                    registers[InputRegister.ADC_RATE_LOW],
                ),
                window_samples=registers[InputRegister.WINDOW_SIZE],
                error_count=u32(
                    registers[InputRegister.ERROR_COUNT_HIGH],
                    registers[InputRegister.ERROR_COUNT_LOW],
                ),
                pause_count=u32(
                    registers[InputRegister.PAUSE_COUNT_HIGH],
                    registers[InputRegister.PAUSE_COUNT_LOW],
                ),
                stats_age_ms=u32(
                    registers[InputRegister.STATS_AGE_HIGH],
                    registers[InputRegister.STATS_AGE_LOW],
                ),
            )

    # -- acquisition settings ---------------------------------------------

    async def read_acquisition_settings(self) -> AcquisitionSettings:
        """
        Read the configured channel, averaging depth and rate mode.

        Returns:
            AcquisitionSettings: The settings plus the sample rate the mode
            selects.

        Raises:
            UnexpectedResponse: If the rate mode is not one the firmware defines.
        """
        async with self._lock:
            registers = await self.read_holding(
                HoldingRegister.SLAVE_ADDRESS,
                HoldingRegister.RATE_MODE + 1,
            )
            rate_mode = registers[HoldingRegister.RATE_MODE]
            sample_rate_hz = RATE_MODE_SAMPLE_RATES.get(rate_mode)
            if sample_rate_hz is None:
                raise UnexpectedResponse(
                    "rate mode",
                    f"unknown value {rate_mode}",
                )
            return AcquisitionSettings(
                channel=registers[HoldingRegister.CHANNEL],
                averaging=registers[HoldingRegister.AVERAGING],
                rate_mode=rate_mode,
                sample_rate_hz=sample_rate_hz,
            )

    async def read_communication_settings(self) -> CommunicationSettings:
        """
        Read the persisted slave address and baud index.

        Returns:
            CommunicationSettings: The address, the index and the baud rate the
            index selects.

        Raises:
            UnexpectedResponse: If the baud index is not one the firmware
                defines.
        """
        async with self._lock:
            registers = await self.read_holding(
                HoldingRegister.SLAVE_ADDRESS,
                HoldingRegister.BAUD_INDEX + 1,
            )
            baud_index = registers[HoldingRegister.BAUD_INDEX]
            baudrate = BAUD_RATES.get(baud_index)
            if baudrate is None:
                raise UnexpectedResponse(
                    "baud index",
                    f"unknown value {baud_index}",
                )
            return CommunicationSettings(
                slave_address=registers[HoldingRegister.SLAVE_ADDRESS],
                baud_index=baud_index,
                baudrate=baudrate,
            )

    async def select_channel(self, channel: int) -> None:
        """
        Select the channel the acquisition measures.

        Args:
            channel: 0 for PA0, 1 for PA1.

        Raises:
            ValueError: If the channel is not 0 or 1, before any bus traffic.
        """
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")
        async with self._lock:
            await self.write_holding(HoldingRegister.CHANNEL, channel)

    async def set_averaging(self, averaging: int) -> None:
        """
        Set the averaging depth of each statistics sample.

        Args:
            averaging: Acquisitions averaged into each sample, 1-4000.

        Raises:
            ValueError: If the value is outside 1-4000, before any bus traffic.
        """
        if not 1 <= averaging <= MAX_CAPTURE_AVERAGING:
            raise ValueError(
                f"averaging must be 1-{MAX_CAPTURE_AVERAGING}, got {averaging}"
            )
        async with self._lock:
            await self.write_holding(HoldingRegister.AVERAGING, averaging)

    async def set_rate_mode(self, mode: int) -> None:
        """
        Set the ADC sample-rate mode.

        Args:
            mode: Rate mode, 0-2. Use ``RATE_MODE_SAMPLE_RATES`` to convert it
                to a sample rate.

        Raises:
            ValueError: If the mode is not one the firmware defines, before any
                bus traffic.
        """
        if mode not in RATE_MODE_SAMPLE_RATES:
            supported = ", ".join(str(value) for value in RATE_MODE_SAMPLE_RATES)
            raise ValueError(f"rate mode must be one of {supported}, got {mode}")
        async with self._lock:
            await self.write_holding(HoldingRegister.RATE_MODE, mode)

    # -- communication settings -------------------------------------------

    async def set_slave_address(self, address: int) -> None:
        """
        Persist a new Modbus slave address.

        The device writes the address to EEPROM, acknowledges the request at the
        OLD address, waits ``REBOOT_DELAY_S`` and restarts, after which it answers
        only at the new address. This instance adopts the new address after the
        delay, so it is ready once the device returns.

        Args:
            address: The new slave address, 1-247.

        Raises:
            ValueError: If the address is outside 1-247 or the transport timeout
                is shorter than ``EEPROM_WRITE_TIMEOUT_S``, before any bus
                traffic.
        """
        if not SLAVE_ADDRESS_MIN <= address <= SLAVE_ADDRESS_MAX:
            raise ValueError(
                f"slave address must be {SLAVE_ADDRESS_MIN}-"
                f"{SLAVE_ADDRESS_MAX}, got {address}"
            )
        self._require_eeprom_timeout()
        async with self._lock:
            await self.write_holding(HoldingRegister.SLAVE_ADDRESS, address)
            await asyncio.sleep(REBOOT_DELAY_S)
            self.address = address

    async def set_baud_index(self, index: int) -> None:
        """
        Persist a new baud index and adopt the selected rate.

        The firmware stores the index, not the baud number. It writes the index
        to EEPROM, acknowledges the request at the OLD rate, waits
        ``REBOOT_DELAY_S`` and restarts, after which it answers only at the new
        rate. This instance rebuilds its serial client at the new rate after the
        delay.

        Args:
            index: Baud index, 0-5. See ``BAUD_RATES``.

        Raises:
            ValueError: If the index is not one the firmware defines or the
                transport timeout is shorter than ``EEPROM_WRITE_TIMEOUT_S``,
                before any bus traffic.
        """
        baudrate = BAUD_RATES.get(index)
        if baudrate is None:
            supported = ", ".join(str(value) for value in BAUD_RATES)
            raise ValueError(f"baud index must be one of {supported}, got {index}")
        self._require_eeprom_timeout()
        async with self._lock:
            await self.write_holding(HoldingRegister.BAUD_INDEX, index)
            await asyncio.sleep(REBOOT_DELAY_S)
            params = self.con_params
            params.baudrate = baudrate
            self.con_params = params

    # -- transport helpers -------------------------------------------------

    async def _read_status_block(self) -> list[int]:
        """Read input registers 0-82 as two frames, 0-49 and 50-82.

        The concatenation is indexed by absolute address: the low frame covers
        indices 0-49 and the high frame 50-82, so a decoder can slice the block
        exactly as the register map names it.
        """
        low = await self.read_inputs(InputRegister.VENDOR_START, 50)
        high = await self.read_inputs(InputRegister.ADC_VREF_MV, 33)
        return low + high

    def _require_eeprom_timeout(self) -> None:
        """Reject a transport timeout too short for an EEPROM write."""
        timeout = self.con_params.timeout
        if timeout is not None and timeout < EEPROM_WRITE_TIMEOUT_S:
            raise ValueError(
                f"EEPROM writes need a transport timeout of at least "
                f"{EEPROM_WRITE_TIMEOUT_S} s or None, got {timeout}"
            )


def _require_capture(
    metadata: CaptureMetadata,
    flags: AdcFlags,
) -> tuple[int, int, int]:
    """Validate the metadata of a finished capture and return its key fields.

    Raises:
        CaptureFailed: If the block number is missing, the channel is not 0 or 1,
            the averaging depth is missing, or the buffer is not marked ready.
    """
    block_number = metadata.block_number
    channel = metadata.channel
    averaging = metadata.averaging
    if block_number is None or block_number < 1:
        raise CaptureFailed("capture reported no block number")
    if channel is None or channel not in (0, 1):
        raise CaptureFailed(f"capture channel {channel} is not 0 or 1")
    if averaging is None or averaging < 1:
        raise CaptureFailed("capture reported no averaging depth")
    if not flags & AdcFlags.CAPTURE_READY:
        raise CaptureFailed("capture buffer is not marked ready")
    return block_number, channel, averaging


def _failure_detail(context: str, metadata: CaptureMetadata) -> str:
    """Name the device-side reason and HAL codes of a failed capture."""
    return (
        f"{context}: stop reason {metadata.stop_reason.name}, "
        f"adc_error={metadata.adc_error}, dma_error={metadata.dma_error}, "
        f"block={metadata.block_number}"
    )
