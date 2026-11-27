"""Decoders for the EBIC_ADC register values.

The firmware reports ADC codes, millivolts and sentinel words; this module turns
them into the physical volts a caller consumes, without touching the bus. A
field equal to :data:`SENTINEL_UNAVAILABLE` decodes to ``None`` before any
scaling, because the sentinel is not a reading. The external divider then maps
pin volts to the physical input: ``physical = pin / divider_ratio``.

Capture columns are unlike the statistics block: an inactive capture channel is
reported as zero, not as the sentinel, so a zero stays ``0.0 V``.

Functions:
    u32: Combine a high and a low word into an unsigned 32-bit value.
    pin_volts: Convert an ADC code to the voltage at the ADC pin.
    physical_v: Convert an ADC code to the voltage at the input connector.
    decode_channel_statistics: Decode one channel's statistics block.
    decode_capture_column: Decode one channel of a capture buffer.
    decode_stop_reason: Decode input register 80.
"""

from collections.abc import Sequence

from ....base import ChannelStatistics
from .constants import (
    ADC_FULL_SCALE_CODE,
    ADC_REFERENCE_V,
    CAPTURE_REGISTER_COUNT,
    DEFAULT_DIVIDER_RATIO,
    SENTINEL_UNAVAILABLE,
    STATS_WINDOW_SAMPLES,
)
from .errors import UnexpectedResponse
from .settings import StopReason

_STATISTICS_REGISTER_COUNT = 8
"""Registers from the channel base that carry a value; b+8 and b+9 are reserved."""

_MEAN_CODE_DIVISOR = 16.0
"""Register b+0 holds the mean ADC code scaled by 16 (Q4)."""

_MV_PER_V = 1000.0


def u32(high: int, low: int) -> int:
    """Combine a high and a low word into an unsigned 32-bit value."""
    return (high << 16) | low


def pin_volts(code: float) -> float:
    """Convert a 12-bit ADC code to the voltage at the ADC pin, in V."""
    return code * ADC_REFERENCE_V / ADC_FULL_SCALE_CODE


def physical_v(code: float, divider_ratio: float = DEFAULT_DIVIDER_RATIO) -> float:
    """Convert a 12-bit ADC code to the voltage at the input connector, in V.

    Args:
        code: ADC code, 0-4095.
        divider_ratio: External divider ratio, 0.6 on the EBIC board.

    Returns:
        float: ``pin_volts(code) / divider_ratio``.

    Raises:
        ValueError: If ``divider_ratio`` is not positive.
    """
    if divider_ratio <= 0:
        raise ValueError(f"divider_ratio must be positive, got {divider_ratio!r}")
    return pin_volts(code) / divider_ratio


def decode_channel_statistics(
    registers: Sequence[int],
    channel: int,
    channel_stats_start: int,
    divider_ratio: float = DEFAULT_DIVIDER_RATIO,
    window_samples: int = STATS_WINDOW_SAMPLES,
) -> ChannelStatistics:
    """Decode one channel's statistics block into physical volts.

    The block starts at ``channel_stats_start``: mean code x16 (Q4) at b+0,
    minimum code at b+1, maximum code at b+2, mean millivolts at b+3, full RMS
    millivolts at b+4, AC RMS millivolts at b+5, peak-to-peak millivolts at b+6,
    and rail samples at b+7. The mean is rebuilt from the Q4 code at b+0 rather
    than from its lower-resolution millivolt mirror at b+3. An inactive channel
    reports every value word as :data:`SENTINEL_UNAVAILABLE`, which decodes to
    ``None``.

    Args:
        registers: Input registers containing the block at ``channel_stats_start``.
        channel: Channel index the block describes, 0 or 1.
        channel_stats_start: Register address of b+0.
        divider_ratio: External divider ratio, 0.6 on the EBIC board.
        window_samples: Number of samples the window contains.

    Returns:
        ChannelStatistics: The decoded statistics.

    Raises:
        ValueError: If the block is shorter than eight registers or the divider
            ratio is not positive.
    """
    if divider_ratio <= 0:
        raise ValueError(f"divider_ratio must be positive, got {divider_ratio!r}")
    end = channel_stats_start + _STATISTICS_REGISTER_COUNT
    if len(registers) < end:
        raise ValueError(
            f"channel {channel} statistics need registers "
            f"{channel_stats_start}-{end - 1}, got {len(registers)}"
        )

    block = registers[channel_stats_start:end]
    mean_code = _available(block[0])
    min_code = _available(block[1])
    max_code = _available(block[2])
    rms_mv = _available(block[4])
    ac_rms_mv = _available(block[5])
    peak_to_peak_mv = _available(block[6])
    rail_samples = _available(block[7])

    return ChannelStatistics(
        channel=channel,
        mean_v=(
            None
            if mean_code is None
            else physical_v(mean_code / _MEAN_CODE_DIVISOR, divider_ratio)
        ),
        min_v=None if min_code is None else physical_v(min_code, divider_ratio),
        max_v=None if max_code is None else physical_v(max_code, divider_ratio),
        rms_v=_mv_to_v(rms_mv, divider_ratio),
        ac_rms_v=_mv_to_v(ac_rms_mv, divider_ratio),
        peak_to_peak_v=_mv_to_v(peak_to_peak_mv, divider_ratio),
        rail_samples=rail_samples,
        window_samples=window_samples,
    )


def decode_capture_column(
    registers: Sequence[int],
    channel: int,
    divider_ratio: float = DEFAULT_DIVIDER_RATIO,
) -> tuple[float, ...]:
    """Decode one channel of an interleaved capture buffer.

    The buffer holds 256 pairs: the even index of each pair is channel 0 and the
    odd index is channel 1. An inactive channel reads zero, which is a real
    ``0.0 V`` point rather than an unavailable value, so no sentinel is applied.

    Args:
        registers: The 512 capture registers, starting at the first pair.
        channel: Channel to extract, 0 or 1.
        divider_ratio: External divider ratio, 0.6 on the EBIC board.

    Returns:
        tuple[float, ...]: 256 points in physical volts, in acquisition order.

    Raises:
        ValueError: If ``channel`` is not 0 or 1, fewer than 512 registers are
            given, or the divider ratio is not positive.
    """
    if channel not in (0, 1):
        raise ValueError(f"capture channel must be 0 or 1, got {channel!r}")
    if len(registers) < CAPTURE_REGISTER_COUNT:
        raise ValueError(
            f"capture needs {CAPTURE_REGISTER_COUNT} registers, got {len(registers)}"
        )
    block = registers[:CAPTURE_REGISTER_COUNT]
    return tuple(physical_v(code, divider_ratio) for code in block[channel::2])


def decode_stop_reason(value: int) -> StopReason:
    """Decode input register 80 into a :class:`StopReason`.

    Raises:
        UnexpectedResponse: If the value is not one the firmware defines.
    """
    try:
        return StopReason(value)
    except ValueError:
        raise UnexpectedResponse("stop reason", f"unknown value {value}") from None


def _available(word: int) -> int | None:
    """Return ``None`` for the unavailable sentinel, the word otherwise."""
    return None if word == SENTINEL_UNAVAILABLE else word


def _mv_to_v(millivolts: int | None, divider_ratio: float) -> float | None:
    """Convert a pin millivolt reading to physical volts, preserving ``None``."""
    if millivolts is None:
        return None
    return millivolts / _MV_PER_V / divider_ratio
