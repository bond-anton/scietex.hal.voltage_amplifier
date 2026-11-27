"""Tests for decoding EBIC_ADC register values into physical volts.

Expected values are computed from the datasheet geometry rather than from the
decoder: a 12-bit code maps through 3.3 V / 4095, then through the external
divider. A sentinel is unavailable, never a reading; a capture zero is a real
zero.
"""

import pytest

try:
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        CAPTURE_POINTS,
        CAPTURE_REGISTER_COUNT,
        DEFAULT_DIVIDER_RATIO,
        SENTINEL_UNAVAILABLE,
        STATS_WINDOW_SAMPLES,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.decode import (
        decode_capture_column,
        decode_channel_statistics,
        decode_stop_reason,
        u32,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AdcFlags,
        StopReason,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        CAPTURE_POINTS,
        CAPTURE_REGISTER_COUNT,
        DEFAULT_DIVIDER_RATIO,
        SENTINEL_UNAVAILABLE,
        STATS_WINDOW_SAMPLES,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.decode import (
        decode_capture_column,
        decode_channel_statistics,
        decode_stop_reason,
        u32,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.settings import (
        AdcFlags,
        StopReason,
    )


def _physical(code: float, divider_ratio: float = DEFAULT_DIVIDER_RATIO) -> float:
    """Expected input voltage of one ADC code, straight from the datasheet."""
    return code * 3.3 / 4095 / divider_ratio


def _statistics_block(
    mean_code: int,
    min_code: int,
    max_code: int,
    rms_mv: int,
) -> list[int]:
    """Build one ten-register statistics block with a deliberately wrong mV mean."""
    return [
        mean_code * 16,
        min_code,
        max_code,
        9999,
        rms_mv,
        0,
        0,
        0,
        0,
        0,
    ]


def test_channel_statistics_decode_codes_and_millivolts_to_physical_volts():
    """Both channels decode from their own base; codes and mV reach volts."""
    registers = [0] * 72
    registers[52:62] = _statistics_block(2048, 100, 4000, 1650)
    registers[57] = 120
    registers[58] = 2400
    registers[59] = 7
    registers[62:72] = _statistics_block(1024, 200, 3000, 825)
    registers[67] = 60
    registers[68] = 1200
    registers[69] = 3

    channel_0 = decode_channel_statistics(registers, 0, 52)
    channel_1 = decode_channel_statistics(registers, 1, 62)

    assert channel_0.channel == 0
    assert channel_0.mean_v == pytest.approx(_physical(2048))
    assert channel_0.min_v == pytest.approx(_physical(100))
    assert channel_0.max_v == pytest.approx(_physical(4000))
    assert channel_0.rms_v == pytest.approx(1.65 / DEFAULT_DIVIDER_RATIO)
    assert channel_0.ac_rms_v == pytest.approx(0.12 / DEFAULT_DIVIDER_RATIO)
    assert channel_0.peak_to_peak_v == pytest.approx(2.4 / DEFAULT_DIVIDER_RATIO)
    assert channel_0.rail_samples == 7
    assert channel_0.window_samples == STATS_WINDOW_SAMPLES

    assert channel_1.channel == 1
    assert channel_1.mean_v == pytest.approx(_physical(1024))
    assert channel_1.min_v == pytest.approx(_physical(200))
    assert channel_1.max_v == pytest.approx(_physical(3000))
    assert channel_1.rms_v == pytest.approx(0.825 / DEFAULT_DIVIDER_RATIO)
    assert channel_1.rail_samples == 3


def test_divider_ratio_one_passes_pin_volts_through():
    """A divider of 1.0 leaves the pin voltage unchanged."""
    registers = _statistics_block(1000, 1000, 1000, 806)

    statistics = decode_channel_statistics(registers, 0, 0, divider_ratio=1.0)

    assert statistics.mean_v == pytest.approx(_physical(1000, 1.0))
    assert statistics.rms_v == pytest.approx(0.806)


def test_all_sentinels_decode_to_none_and_window_is_copied():
    """An inactive channel reports sentinels, which are not scaled."""
    registers = [SENTINEL_UNAVAILABLE] * 10

    statistics = decode_channel_statistics(registers, 1, 0, window_samples=128)

    assert statistics.mean_v is None
    assert statistics.min_v is None
    assert statistics.max_v is None
    assert statistics.rms_v is None
    assert statistics.ac_rms_v is None
    assert statistics.peak_to_peak_v is None
    assert statistics.rail_samples is None
    assert statistics.window_samples == 128


def test_single_sentinel_clears_only_its_own_field():
    """A sentinel inside an otherwise valid window affects one field."""
    registers = _statistics_block(2048, 100, 4000, 1650)
    registers[0] = SENTINEL_UNAVAILABLE
    registers[7] = 7

    statistics = decode_channel_statistics(registers, 0, 0)

    assert statistics.mean_v is None
    assert statistics.min_v == pytest.approx(_physical(100))
    assert statistics.rail_samples == 7


def test_short_statistics_block_is_rejected():
    """Eight registers are required from the channel base."""
    with pytest.raises(ValueError, match="statistics need"):
        decode_channel_statistics([0] * 7, 0, 0)


def test_capture_column_takes_even_indices_for_channel_zero():
    """Channel 0 is the first sample of every pair, in acquisition order."""
    registers = [0] * CAPTURE_REGISTER_COUNT
    for index in range(CAPTURE_POINTS):
        registers[2 * index] = index
        registers[2 * index + 1] = 4095 - index

    column = decode_capture_column(registers, 0)

    assert len(column) == CAPTURE_POINTS
    assert column[0] == 0.0
    assert column[1] == pytest.approx(_physical(1))
    assert column[-1] == pytest.approx(_physical(CAPTURE_POINTS - 1))


def test_capture_column_takes_odd_indices_for_channel_one():
    """Channel 1 is the second sample of every pair."""
    registers = [0] * CAPTURE_REGISTER_COUNT
    for index in range(CAPTURE_POINTS):
        registers[2 * index] = index
        registers[2 * index + 1] = 4095 - index

    column = decode_capture_column(registers, 1)

    assert len(column) == CAPTURE_POINTS
    assert column[0] == pytest.approx(3.3 / DEFAULT_DIVIDER_RATIO)
    assert column[-1] == pytest.approx(_physical(4095 - (CAPTURE_POINTS - 1)))


def test_capture_full_scale_code_maps_through_the_divider():
    """A full-scale code is 3.3 V at the pin, divided at the connector."""
    registers = [4095] * CAPTURE_REGISTER_COUNT

    assert decode_capture_column(registers, 0)[0] == pytest.approx(3.3 / 0.6)
    assert decode_capture_column(registers, 0, divider_ratio=1.0)[0] == pytest.approx(
        3.3
    )


def test_short_capture_block_is_rejected():
    """A capture carries exactly 512 registers, two per point."""
    with pytest.raises(ValueError, match="capture needs"):
        decode_capture_column([0] * (CAPTURE_REGISTER_COUNT - 1), 0)


def test_unknown_capture_channel_is_rejected():
    """Only the two ADC channels exist."""
    with pytest.raises(ValueError, match="channel"):
        decode_capture_column([0] * CAPTURE_REGISTER_COUNT, 2)


def test_u32_combines_high_and_low_words():
    """The high word occupies the upper 16 bits."""
    assert u32(0x1234, 0x5678) == 0x12345678
    assert u32(0, 1) == 1


def test_flag_bits_match_the_firmware():
    """The ADC flag word uses the documented bit positions."""
    assert AdcFlags.STATS_READY == 0x0001
    assert AdcFlags.ADC_ERROR == 0x0002
    assert AdcFlags.CAPTURE_READY == 0x0004
    assert AdcFlags.CAPTURE_RUNNING == 0x0008
    assert AdcFlags.RAIL_SAMPLES == 0x0010
    assert AdcFlags(0x0005) == AdcFlags.STATS_READY | AdcFlags.CAPTURE_READY


def test_stop_reason_values_match_the_firmware():
    """Every documented stop reason decodes to its member."""
    assert StopReason.NONE == 0
    assert StopReason.HAL_ERROR == 1
    assert StopReason.DMA_MISSED == 2
    assert decode_stop_reason(1) is StopReason.HAL_ERROR


def test_unknown_stop_reason_is_rejected():
    """A value outside the firmware enumeration cannot be trusted."""
    with pytest.raises(UnexpectedResponse, match="stop reason"):
        decode_stop_reason(3)
