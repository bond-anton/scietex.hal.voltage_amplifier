"""Decoded settings and status values of the EBIC_ADC firmware.

The dataclasses here are plain value objects: nothing in this module touches the
bus or a register address. :class:`AdcFlags` and :class:`StopReason` decode the
bit fields and enumerations the firmware reports; the dataclasses carry the
decoded values to a caller.

Classes:
    AdcFlags: Bits of input register 38.
    StopReason: Values of input register 80.
    CommunicationSettings: Persisted slave address and baud rate.
    AcquisitionSettings: Channel, averaging and rate configuration.
    DeviceStatus: Statistics-block status snapshot.
    CaptureMetadata: What the last capture ran with and why it stopped.
"""

from dataclasses import dataclass
from enum import IntEnum, IntFlag


class AdcFlags(IntFlag):
    """Bits of input register 38."""

    STATS_READY = 0x0001
    """A fresh statistics window is available."""

    ADC_ERROR = 0x0002
    """The ADC reported an error."""

    CAPTURE_READY = 0x0004
    """The capture buffer is complete and readable."""

    CAPTURE_RUNNING = 0x0008
    """A capture is in progress."""

    RAIL_SAMPLES = 0x0010
    """At least one sample of the window sat at a supply rail."""


class StopReason(IntEnum):
    """Values of input register 80; why the last capture stopped."""

    NONE = 0
    """The capture has not stopped or stopped normally."""

    HAL_ERROR = 1
    """The HAL reported an error while capturing."""

    DMA_MISSED = 2
    """A DMA transfer was missed."""


@dataclass(frozen=True, slots=True)
class CommunicationSettings:
    """Persisted communication settings.

    Both values are written to EEPROM and take effect only after the firmware
    restarts.

    Attributes:
        slave_address: Modbus slave address, 1-247.
        baud_index: Baud-rate index, 0-5.
        baudrate: Baud rate the index selects, in baud.
    """

    slave_address: int
    baud_index: int
    baudrate: int


@dataclass(frozen=True, slots=True)
class AcquisitionSettings:
    """Channel and rate configuration of the acquisition.

    Attributes:
        channel: Acquisition channel: 0 = PA0, 1 = PA1.
        averaging: Acquisitions averaged into each statistics sample, 1-4000.
        rate_mode: Sample-rate mode, 0-2.
        sample_rate_hz: ADC sample rate the mode selects, in samples per second.
    """

    channel: int
    averaging: int
    rate_mode: int
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """Status of the device-side statistics block.

    Attributes:
        flags: Bits of input register 38.
        stats_block: Number of the statistics block the device has completed.
        adc_rate_hz: ADC sample rate in samples per second.
        window_samples: Number of samples in the statistics window.
        error_count: ADC conversion errors counted so far.
        pause_count: Acquisition pauses counted so far.
        stats_age_ms: Age of the current statistics window in ms.
    """

    flags: AdcFlags
    stats_block: int
    adc_rate_hz: int
    window_samples: int
    error_count: int
    pause_count: int
    stats_age_ms: int


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    """What the last capture ran with and why it stopped.

    A field is ``None`` when the device reports the same sentinel the ADC uses
    for an unavailable value, before any capture has run.

    Attributes:
        channel: Channel the capture ran on, or ``None`` when unavailable.
        averaging: Averaging depth the capture ran with, or ``None``.
        block_number: Capture block number, or ``None``.
        stop_reason: Why the capture stopped.
        adc_error: HAL ADC error code, 0 when the HAL reported none.
        dma_error: HAL DMA error code, 0 when the HAL reported none.
    """

    channel: int | None
    averaging: int | None
    block_number: int | None
    stop_reason: StopReason
    adc_error: int
    dma_error: int
