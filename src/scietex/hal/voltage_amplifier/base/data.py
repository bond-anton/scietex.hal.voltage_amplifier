"""Voltage amplifier data structures.

:class:`AmplifierState` is a snapshot of what the device reports at one moment;
:class:`FilterSetting` is one entry of the filter list inside it.

``None`` means "this driver cannot report the field", not "zero" and not "off".
A field that is ``None`` has no known value at all, so a caller must check for
``None`` rather than treating a missing reading as an idle amplifier.

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
