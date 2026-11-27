"""
Voltage amplifier protocol.

This module defines the transport-agnostic contract every voltage amplifier
driver implements, plus the capability conveniences that tell a caller which
optional control operations a given amplifier supports. The contract is
deliberately minimal: a driver must be able to connect, disconnect and read the
state snapshot. Every control operation beyond that is advertised through
:attr:`VoltageAmplifierProtocol.capabilities`, so a caller can reject an
unsupported command before dispatching it instead of catching an exception
after the fact.

Every driver I/O call is a coroutine, because the shared transport is
asynchronous: callers must ``await`` :meth:`connect`, :meth:`close`,
:meth:`read_data` and every optional accessor, and an unimplemented optional
accessor raises ``NotImplementedError`` when awaited. The ``supports_*`` and
``can_*`` properties are pure capability checks over :attr:`capabilities` and
stay synchronous.

Two failure signals carry different meanings and must not be confused. A
``NotImplementedError`` raised by an optional method means "this driver does not
implement that capability": the feature is absent, so the caller should consult
the capability set and take a different path. A ``None`` field in the snapshot
returned by :meth:`VoltageAmplifierProtocol.read_data` means "this driver cannot
report that value": the feature may well exist and be controllable, but the
current reading is unknown, so the caller must not substitute zero or ``False``.

Output overload is an exception to the method-based surface: it is read only
through ``read_data()`` and has no dedicated accessor. The capability set says
whether the driver reports it; the snapshot says what it currently is.

Filter kinds are read from ``capabilities.filters.types``; there is no
per-kind ``supports_*`` property, so adding a :class:`FilterType` never changes
this class.

The voltage range and the output swing are static geometry rather than
controllable features, so they have no ``supports_*`` property either: a caller
reads ``capabilities.input_voltage`` and ``capabilities.output_voltage``
directly. The gain is the only scalable quantity: the amplifier multiplies the
input voltage by it, optionally filtering the signal and inverting the polarity.

Measurement is an optional group of its own, advertised through
``capabilities.measurement``: :meth:`get_statistics` reads the statistics window
of the channel the device currently measures, without selecting a channel, and
:meth:`capture` triggers an acquisition, waits for it to finish and returns the
buffered points, so calling it changes device state rather than only reading it.

Classes:
    VoltageAmplifierProtocol: The runtime-checkable protocol every voltage
        amplifier driver satisfies.
"""

from typing import ClassVar, Protocol, runtime_checkable

from .capabilities import FilterType, VoltageAmplifierCapabilities
from .data import AmplifierState, Capture, ChannelStatistics, FilterSetting


@runtime_checkable
class VoltageAmplifierProtocol(Protocol):
    """
    Contract implemented by every voltage amplifier driver.

    The three core methods are mandatory. Every optional method raises
    ``NotImplementedError`` by default and is advertised through
    :attr:`capabilities`; callers must consult the capability set before
    invoking one. Every I/O method is a coroutine; the convenience properties
    are synchronous.

    Attributes:
        capabilities: What this amplifier can do, grouped by function. A driver
            declares it as a class attribute, so the whole capability set is
            known before an instance connects to hardware.
    """

    capabilities: ClassVar[VoltageAmplifierCapabilities]

    @property
    def supports_gain(self) -> bool:
        """Whether the amplifier exposes adjustable voltage gain."""
        return bool(self.capabilities.gain)

    @property
    def supports_filters(self) -> bool:
        """Whether the amplifier exposes any signal filter control."""
        filters = self.capabilities.filters
        return bool(filters.types) or bool(filters.enabled) or bool(filters.cutoff)

    @property
    def supports_output_overload(self) -> bool:
        """Whether the amplifier reports the output-overload flag."""
        return bool(self.capabilities.output_overload)

    @property
    def supports_invert_output(self) -> bool:
        """Whether the amplifier exposes output-polarity inversion."""
        return bool(self.capabilities.invert_output)

    @property
    def supports_statistics(self) -> bool:
        """Whether the amplifier reports statistics for the measured channel."""
        return bool(self.capabilities.measurement.statistics)

    @property
    def supports_capture(self) -> bool:
        """Whether the amplifier can return a buffered waveform capture."""
        return bool(self.capabilities.measurement.capture)

    @property
    def can_set_gain(self) -> bool:
        """Whether the voltage gain can be written."""
        return self.capabilities.gain.gain.access.write

    @property
    def can_set_filter(self) -> bool:
        """Whether a filter can be switched or its cutoff written."""
        return (
            self.capabilities.filters.enabled.write
            or self.capabilities.filters.cutoff.write
        )

    @property
    def can_set_invert_output(self) -> bool:
        """Whether output-polarity inversion can be written."""
        return self.capabilities.invert_output.write

    @property
    def can_trigger_capture(self) -> bool:
        """Whether a waveform capture can be started."""
        return self.capabilities.measurement.capture.write

    async def connect(self) -> None:
        """Open the transport to the amplifier."""
        raise NotImplementedError(f"{type(self).__name__} must implement connect()")

    async def close(self) -> None:
        """Close the transport to the amplifier."""
        raise NotImplementedError(f"{type(self).__name__} must implement close()")

    async def read_data(self) -> AmplifierState:
        """
        Read a snapshot of the amplifier's current values.

        Returns:
            AmplifierState: The current snapshot. A field is ``None`` when the
                driver cannot report it.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement read_data()")

    async def get_gain(self) -> float:
        """
        Read the voltage gain.

        Returns:
            float: The dimensionless gain in V/V.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_gain(); capability: gain"
        )

    async def set_gain(self, gain: float) -> None:
        """
        Set the voltage gain.

        Args:
            gain: The dimensionless gain in V/V, normally one of
                ``capabilities.gain.gain.options`` or inside its bounds.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement set_gain(); capability: gain"
        )

    async def get_filter(self, filter_type: FilterType) -> FilterSetting:
        """
        Read one filter's settings.

        Args:
            filter_type: The filter response to read.

        Returns:
            FilterSetting: The filter entry, with its cutoff corners in Hz.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_filter(); "
            "capability: filters"
        )

    async def set_filter(self, setting: FilterSetting) -> None:
        """
        Write one filter's settings.

        Args:
            setting: The filter entry to apply. ``setting.type`` selects the
                filter, ``enabled`` switches it and the cutoff corners set its
                frequency.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement set_filter(); "
            "capability: filters"
        )

    async def get_inverted(self) -> bool:
        """
        Read the output-polarity inversion state.

        Returns:
            bool: Whether the output is inverted.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_inverted(); "
            "capability: invert_output"
        )

    async def set_inverted(self, inverted: bool) -> None:
        """
        Set the output-polarity inversion state.

        Args:
            inverted: Whether to invert the output.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement set_inverted(); "
            "capability: invert_output"
        )

    async def get_statistics(self) -> ChannelStatistics:
        """
        Read the statistics window of the channel currently measured.

        The channel is chosen by the device, not by this call, so the returned
        ``channel`` field identifies which one the statistics describe.

        Returns:
            ChannelStatistics: The statistics, with a field ``None`` when no fresh
                window or an inactive channel leaves it unknown.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_statistics(); "
            "capability: measurement"
        )

    async def capture(self) -> Capture:
        """
        Trigger a waveform capture, wait for it and read the buffered points.

        This call is not a passive read: it starts an acquisition on the device,
        which may overwrite the previous buffer and occupy the measurement channel
        until the capture finishes.

        Returns:
            Capture: The buffered points in physical volts.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement capture(); "
            "capability: measurement"
        )
