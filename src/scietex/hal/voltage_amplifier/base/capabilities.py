"""
Voltage amplifier capability model.

An amplifier advertises what it can do through a single
:class:`VoltageAmplifierCapabilities` value, grouped by function rather than by
method name. The grouping is semantic: a driver maps its own vocabulary onto
these groups, so a caller can ask "can this amplifier change its gain?" without
knowing whether the underlying protocol calls the setting a factor, a code, or a
register.

Two kinds of surface are modelled separately. A switch is an :class:`Access`,
which distinguishes "readable" from "writeable" because many devices expose a
setting read-only. A switch that also takes a value is a
:class:`SelectableCapabilities`, which adds the domain of accepted values.

A third kind is static geometry: a limit that never changes while the device
runs, such as the input voltage range or the output voltage swing, is capability
data rather than state. It is read from the capability set directly instead of
through a protocol method or a ``supports_*`` property.

The groups are frozen dataclasses so a driver declares them once as a class
attribute and callers cannot mutate them.

Classes:
    FilterType: The filter responses an amplifier can apply.
    Access: Whether a setting can be read and whether it can be written.
    SelectableCapabilities: An access plus the values it accepts.
    OutputVoltageCapabilities: Static output voltage swing limits.
    InputVoltageCapabilities: Static input voltage range limits.
    VoltageGainCapabilities: Gain selection with the bounds it accepts.
    FilterCapabilities: Signal-filter surface.
    IndicationCapabilities: A read-only status flag.
    MeasurementCapabilities: Statistics and waveform-capture surface.
    VoltageAmplifierCapabilities: The complete capability set for one amplifier.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar


class FilterType(str, Enum):
    """The filter responses an amplifier can apply.

    The member value is the wire spelling a driver sends; it doubles as the
    serialised form because the class inherits from ``str``.
    """

    LOW_PASS = "low_pass"
    HIGH_PASS = "high_pass"
    BAND_PASS = "band_pass"


@dataclass(frozen=True, slots=True)
class Access:
    """
    Whether a setting can be read and whether it can be written.

    Attributes:
        read (bool): Whether the current value can be read back.
        write (bool): Whether the value can be set.
    """

    read: bool = False
    write: bool = False

    def __bool__(self) -> bool:
        """Return whether the setting is supported in either direction."""
        return self.read or self.write


T = TypeVar("T")


@dataclass(frozen=True, slots=True, eq=False)
class SelectableCapabilities(Generic[T]):
    """
    An access plus the values it accepts.

    Attributes:
        access (Access): Whether the setting is readable and writeable.
        options (tuple[T, ...]): The values the device accepts, in the device's
            own order and native units. An empty tuple means the device does
            not enumerate its domain, which is typical of a continuous setting
            such as a gain or a cutoff frequency; it does **not** mean the
            device accepts no value. A caller that needs the legal domain of an
            enumerating device reads the options, and against a device with an
            empty tuple it must rely on the state snapshot instead.
    """

    access: Access = field(default_factory=Access)
    options: tuple[T, ...] = ()

    def __bool__(self) -> bool:
        """Return whether the setting is supported in either direction."""
        return bool(self.access)


@dataclass(frozen=True, slots=True)
class OutputVoltageCapabilities:
    """
    Static output voltage swing limits.

    The limits are geometry rather than runtime state, which is why they live in
    the capability set: they do not change while the device runs. The output
    voltage the device currently reports is a field of the state snapshot.

    Attributes:
        min_v (float | None): Most negative output voltage in V, or ``None`` when
            the driver does not know the limit.
        max_v (float | None): Most positive output voltage in V, or ``None`` when
            the driver does not know the limit.
    """

    min_v: float | None = None
    max_v: float | None = None


@dataclass(frozen=True, slots=True)
class InputVoltageCapabilities:
    """
    Static input voltage range limits.

    The input range is the span the front end accepts before it clips or
    saturates. Like the output swing it is geometry rather than runtime state,
    so it belongs to the capability set, while the input voltage the device
    currently reports is a field of the state snapshot.

    Attributes:
        min_v (float | None): Most negative input voltage in V, or ``None`` when
            the driver does not know the limit.
        max_v (float | None): Most positive input voltage in V, or ``None`` when
            the driver does not know the limit.
    """

    min_v: float | None = None
    max_v: float | None = None


@dataclass(frozen=True, slots=True)
class VoltageGainCapabilities:
    """
    Gain selection with the bounds it accepts.

    Gain is dimensionless and expressed in V/V: the output voltage divided by
    the input voltage. A device may enumerate its gains, describe them as a
    bounded continuous range, or do neither, and the three cases are
    distinguishable: enumerated gains appear in ``gain.options``, a bounded
    range appears in ``min_gain`` and ``max_gain``, and an empty ``options``
    with both bounds unknown means the domain is unknown rather than empty.

    ``step`` documents the smallest increment the device resolves; it never
    quantises anything at this layer, so :meth:`accepts` does not round.

    Attributes:
        gain (SelectableCapabilities[float]): Selectable gains as dimensionless
            factors. An empty tuple means the domain is continuous or not
            enumerated.
        min_gain (float | None): Lowest settable gain in V/V, or ``None`` when
            the driver does not know the bound.
        max_gain (float | None): Highest settable gain in V/V, or ``None`` when
            the driver does not know the bound.
        step (float | None): Smallest gain increment the device resolves, or
            ``None`` when unknown.
        default_gain (float | None): The gain the device applies at power-on or
            reset, or ``None`` when the driver does not know it. It is never
            assumed to be unity.
    """

    gain: SelectableCapabilities[float] = field(default_factory=SelectableCapabilities)
    min_gain: float | None = None
    max_gain: float | None = None
    step: float | None = None
    default_gain: float | None = None

    def __bool__(self) -> bool:
        """Return whether the gain is selectable or bounded."""
        return bool(self.gain) or self.min_gain is not None or self.max_gain is not None

    def accepts(self, gain: float) -> bool | None:
        """
        Tell whether the device accepts a gain.

        Enumerated options take precedence over the bounds, because a device
        that lists its gains accepts exactly those values. When the options are
        empty the bounds are used, and a bound that is ``None`` leaves that side
        unbounded.

        Args:
            gain: The dimensionless gain in V/V to test.

        Returns:
            bool | None: Whether the gain lies in the advertised domain, or
            ``None`` when the driver enumerates no option and knows no bound, so
            the domain is unknown rather than empty.
        """
        if self.gain.options:
            return gain in self.gain.options
        if self.min_gain is None and self.max_gain is None:
            return None
        below_min = self.min_gain is not None and gain < self.min_gain
        above_max = self.max_gain is not None and gain > self.max_gain
        return not (below_min or above_max)


@dataclass(frozen=True, slots=True)
class FilterCapabilities:
    """
    Signal-filter surface.

    Attributes:
        types (tuple[FilterType, ...]): Filter responses the amplifier supports.
            An empty tuple means no filter is available.
        enabled (Access): Whether the filter can be read and switched on or off.
        cutoff (Access): Whether the filter cutoff frequency can be read and set.
            The frequency domain is carried by the state snapshot, so it is not
            enumerated here.
    """

    types: tuple[FilterType, ...] = ()
    enabled: Access = field(default_factory=Access)
    cutoff: Access = field(default_factory=Access)


@dataclass(frozen=True, slots=True)
class IndicationCapabilities:
    """
    A read-only status flag.

    Attributes:
        read (bool): Whether the flag can be read.
    """

    read: bool = False

    def __bool__(self) -> bool:
        """Return whether the flag is reported."""
        return self.read


@dataclass(frozen=True, slots=True)
class MeasurementCapabilities:
    """
    Statistics and waveform-capture surface.

    Statistics summarise the channel the device currently measures; they are
    readable only, because the window is a device-side computation rather than a
    setting. Capture is a buffered acquisition: when the device can be told to
    start one, ``capture.write`` is set and the trigger is a write, while
    ``capture.read`` says the resulting points can be read back.

    The two limits are static geometry in the same sense as the voltage ranges:
    they describe what the device buffers, not what it currently holds. A limit
    of zero means the driver does not know it, not that the device can capture
    nothing; the corresponding ``Access`` bit is what advertises the feature.

    Attributes:
        statistics (Access): Whether the statistics window can be read.
        capture (Access): Whether a capture can be read and whether a trigger can
            be written.
        max_capture_points (int): Largest number of points one capture returns, or
            ``0`` when the driver does not know the limit.
        max_capture_averaging (int): Largest averaging depth the device accepts,
            or ``0`` when the driver does not know the limit.
    """

    statistics: Access = field(default_factory=Access)
    capture: Access = field(default_factory=Access)
    max_capture_points: int = 0
    max_capture_averaging: int = 0

    def __bool__(self) -> bool:
        """Return whether statistics or capture is advertised."""
        return bool(self.statistics) or bool(self.capture)


@dataclass(frozen=True, slots=True)
class VoltageAmplifierCapabilities:
    """
    The complete capability set for one amplifier.

    Every group defaults to "unsupported", so a driver declares only what it
    actually implements.

    A capability answers whether a feature is available; it does not answer
    whether the feature is currently on. A present feature that is switched off
    is truthy here (``bool(...) is True``) while the state flag in the snapshot
    is ``False``. Filtering on capabilities and reading the state are therefore
    both required to act correctly.

    Attributes:
        gain (VoltageGainCapabilities): Gain selection, in V/V.
        input_voltage (InputVoltageCapabilities): Static input voltage range
            limits, in V.
        output_voltage (OutputVoltageCapabilities): Static output voltage swing
            limits, in V.
        filters (FilterCapabilities): Signal-filter surface.
        output_overload (IndicationCapabilities): Output-overload flag.
        invert_output (Access): Whether the output polarity can be read and set.
        measurement (MeasurementCapabilities): Statistics and waveform-capture
            surface.
    """

    gain: VoltageGainCapabilities = field(default_factory=VoltageGainCapabilities)
    input_voltage: InputVoltageCapabilities = field(
        default_factory=InputVoltageCapabilities
    )
    output_voltage: OutputVoltageCapabilities = field(
        default_factory=OutputVoltageCapabilities
    )
    filters: FilterCapabilities = field(default_factory=FilterCapabilities)
    output_overload: IndicationCapabilities = field(
        default_factory=IndicationCapabilities
    )
    invert_output: Access = field(default_factory=Access)
    measurement: MeasurementCapabilities = field(
        default_factory=MeasurementCapabilities
    )

    def max_output_swing_v(self) -> float | None:
        """
        Compute the largest symmetric output excursion.

        Returns:
            float | None: The smaller of ``|min_v|`` and ``|max_v|``, in V, which
            is how far the output can swing in either direction without
            clipping. ``None`` when either limit is unknown, because a symmetric
            excursion then cannot be guaranteed.
        """
        limits = self.output_voltage
        if limits.min_v is None or limits.max_v is None:
            return None
        return min(abs(limits.min_v), abs(limits.max_v))

    def max_input_voltage_v(self, gain: float) -> float | None:
        """
        Compute the input voltage that drives the output to its swing limit.

        The input range and the output swing divided by the gain are two
        independent ceilings, and the smaller one binds first.

        Args:
            gain: Dimensionless voltage gain in V/V.

        Returns:
            float | None: The input-voltage ceiling in V, or ``None`` when
            neither the input range nor the output swing is known.

        Raises:
            ValueError: If ``gain`` is zero or negative, since such a gain
                cannot scale a voltage.
        """
        if gain <= 0:
            raise ValueError(f"gain must be positive, got {gain!r} V/V")
        candidates: list[float] = []
        limits = self.input_voltage
        if limits.min_v is not None and limits.max_v is not None:
            candidates.append(min(abs(limits.min_v), abs(limits.max_v)))
        swing_v = self.max_output_swing_v()
        if swing_v is not None:
            candidates.append(swing_v / gain)
        if not candidates:
            return None
        return min(candidates)
