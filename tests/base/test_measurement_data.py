"""Tests for the measurement value structs."""

import msgspec
import pytest

try:
    from src.scietex.hal.voltage_amplifier.base.data import (
        Capture,
        ChannelStatistics,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base.data import Capture, ChannelStatistics


def test_capture_defaults_have_no_timing():
    """An empty capture has no points and no rate to derive timing from."""
    capture = Capture()

    assert capture.channel == 0
    assert capture.points == ()
    assert capture.averaging == 1
    assert capture.sample_rate_hz is None
    assert capture.block_number is None
    assert capture.point_interval_s is None
    assert capture.duration_s is None


@pytest.mark.parametrize("sample_rate_hz", [0.0, -1.0])
def test_capture_timing_is_unknown_for_a_non_positive_rate(sample_rate_hz):
    """A non-positive rate cannot define a point interval."""
    capture = Capture(points=(1.0,), sample_rate_hz=sample_rate_hz)

    assert capture.point_interval_s is None
    assert capture.duration_s is None


def test_capture_duration_multiplies_points_by_the_point_interval():
    """256 points averaged 781 times at 100 kHz span 1.99936 s."""
    capture = Capture(
        averaging=781,
        sample_rate_hz=100_000.0,
        points=(0.0,) * 256,
    )

    assert capture.point_interval_s == pytest.approx(0.00781)
    assert capture.duration_s == pytest.approx(1.99936)


def test_capture_is_frozen():
    """A capture cannot be mutated in place."""
    capture = Capture()

    # msgspec frozen structs raise AttributeError, not dataclasses.FrozenInstanceError.
    with pytest.raises(AttributeError, match="immutable type"):
        capture.averaging = 2


def test_capture_round_trips_through_builtins():
    """Serialising to plain builtins and back preserves every field."""
    capture = Capture(
        channel=1,
        points=(0.0, 0.5, -0.5),
        averaging=781,
        sample_rate_hz=100_000.0,
        block_number=7,
    )

    builtins = msgspec.to_builtins(capture)
    restored = msgspec.convert(builtins, type=Capture)

    assert restored == capture
    assert builtins["points"] == (0.0, 0.5, -0.5)


def test_channel_statistics_defaults_are_unknown():
    """Every scalar defaults to ``None``: an empty window is not a measured zero."""
    statistics = ChannelStatistics()

    assert statistics.channel == 0
    for name in (
        "mean_v",
        "min_v",
        "max_v",
        "rms_v",
        "ac_rms_v",
        "peak_to_peak_v",
        "rail_samples",
        "window_samples",
    ):
        assert getattr(statistics, name) is None


def test_channel_statistics_is_frozen():
    """A statistics snapshot cannot be mutated in place."""
    statistics = ChannelStatistics()

    with pytest.raises(AttributeError, match="immutable type"):
        statistics.mean_v = 0.0
