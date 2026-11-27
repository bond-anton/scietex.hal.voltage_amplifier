"""Voltage amplifier data structures.

:class:`AmplifierState` is a snapshot of what the device reports at one moment;
:class:`FilterSetting` is one entry of the filter list inside it.
:class:`ChannelStatistics` summarises the window the device currently measures,
and :class:`Capture` holds one buffered waveform acquisition.

``None`` means "this driver cannot report the field", not "zero" and not "off".
A field that is ``None`` has no known value at all, so a caller must check for
``None`` rather than treating a missing reading as an idle amplifier. The same
rule holds for a statistics field: ``None`` means no fresh window or an inactive
channel, never a measured zero.

Filter fields are device-native engineering units. No unit or scale is invented
at this layer: a driver that speaks counts or register codes stores the value it
reads and documents the conversion in the driver.
"""

import msgspec

from .capabilities import FilterType


class FilterSetting(msgspec.Struct, frozen=True, kw_only=True):
    """One filter in the amplifier's signal path.

    Attributes:
        type: The filter response the entry configures.
        enabled: Whether this filter is currently switched on. The field is a
            plain ``bool``, not ``bool | None``: a driver either reports the
            filter entry together with its on/off state or omits the entry.
        low_cutoff_hz: Lower cutoff frequency in Hz, or ``None`` when the filter
            has no lower corner or the driver does not report it. A high-pass
            or band-pass filter may report only one corner.
        high_cutoff_hz: Upper cutoff frequency in Hz, or ``None`` when the
            filter has no upper corner or the driver does not report it.
    """

    type: FilterType
    enabled: bool = False
    low_cutoff_hz: float | None = None
    high_cutoff_hz: float | None = None


class AmplifierState(msgspec.Struct, frozen=True, kw_only=True):
    """A snapshot of the values an amplifier reports.

    Each field is ``None`` when the driver cannot report it. ``None`` is not
    ``0.0`` and not ``False``: an unreported gain is unknown, not unity, and an
    unreadable overload flag is unknown, not clear.

    Attributes:
        input_voltage_v: Input voltage in V, or ``None`` when unreadable.
        output_voltage_v: Output voltage in V, or ``None`` when unreadable.
        output_current_a: Output current in A, or ``None`` when unreadable.
        gain: Dimensionless voltage gain in V/V, or ``None`` when the driver
            does not report it. ``None`` is not ``1.0``: an unreported gain may
            be anything, while unity is only the value the device applies at
            power-on when the driver says so.
        filters: Configured filters, in the order the device reports them.
        output_overload: Output-overload flag, or ``None`` when unreadable.
        inverted: Whether the output polarity is inverted, or ``None`` when
            unreadable.
    """

    input_voltage_v: float | None = None
    output_voltage_v: float | None = None
    output_current_a: float | None = None
    gain: float | None = None
    filters: tuple[FilterSetting, ...] = ()
    output_overload: bool | None = None
    inverted: bool | None = None


class ChannelStatistics(msgspec.Struct, frozen=True, kw_only=True):
    """Statistics over the device-side measurement window.

    Every scalar is ``None`` when the window is empty, the channel is inactive, or
    the driver cannot report it. ``None`` is never a measured zero: a caller must
    not treat a missing mean as an input sitting at 0 V.

    Attributes:
        channel: Index of the channel these statistics describe.
        mean_v: Mean voltage in V, or ``None``.
        min_v: Minimum voltage in V, or ``None``.
        max_v: Maximum voltage in V, or ``None``.
        rms_v: Root-mean-square voltage in V, or ``None``.
        ac_rms_v: AC root-mean-square voltage in V, or ``None``.
        peak_to_peak_v: Peak-to-peak voltage in V, or ``None``.
        rail_samples: Number of samples at a supply rail in the window, or
            ``None`` when the driver does not count them.
        window_samples: Number of samples the window contains, or ``None`` when
            the driver does not report it.
    """

    channel: int = 0
    mean_v: float | None = None
    min_v: float | None = None
    max_v: float | None = None
    rms_v: float | None = None
    ac_rms_v: float | None = None
    peak_to_peak_v: float | None = None
    rail_samples: int | None = None
    window_samples: int | None = None


class Capture(msgspec.Struct, frozen=True, kw_only=True):
    """One buffered waveform acquisition, in physical volts.

    Attributes:
        channel: Index of the channel that was captured.
        points: Captured samples in V, in acquisition order.
        averaging: Number of acquisitions averaged into each point.
        sample_rate_hz: Sampling rate in Hz, or ``None`` when the driver does not
            report it.
        block_number: Device block number the points came from, or ``None`` when
            the driver does not report it.
    """

    channel: int = 0
    points: tuple[float, ...] = ()
    averaging: int = 1
    sample_rate_hz: float | None = None
    block_number: int | None = None

    @property
    def point_interval_s(self) -> float | None:
        """Time between adjacent points in s, or ``None`` when the rate is unknown."""
        if self.sample_rate_hz is None or self.sample_rate_hz <= 0:
            return None
        return self.averaging / self.sample_rate_hz

    @property
    def duration_s(self) -> float | None:
        """Captured span in s, or ``None`` when the point interval is unknown."""
        interval = self.point_interval_s
        if interval is None:
            return None
        return len(self.points) * interval
