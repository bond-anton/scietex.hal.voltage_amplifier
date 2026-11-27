"""In-memory amplifiers used by the test suite.

The fakes model the driver contract rather than hardware. A feature the
capability set does not advertise raises ``NotImplementedError`` from the
corresponding method, and a value the fake cannot report stays ``None`` in the
snapshot.
"""

from typing import ClassVar

from msgspec.structs import replace

try:
    from src.scietex.hal.voltage_amplifier.base import (
        Access,
        AmplifierState,
        Capture,
        ChannelStatistics,
        FilterCapabilities,
        FilterSetting,
        FilterType,
        IndicationCapabilities,
        InputVoltageCapabilities,
        MeasurementCapabilities,
        OutputVoltageCapabilities,
        SelectableCapabilities,
        VoltageAmplifierCapabilities,
        VoltageAmplifierProtocol,
        VoltageGainCapabilities,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base import (
        Access,
        AmplifierState,
        Capture,
        ChannelStatistics,
        FilterCapabilities,
        FilterSetting,
        FilterType,
        IndicationCapabilities,
        InputVoltageCapabilities,
        MeasurementCapabilities,
        OutputVoltageCapabilities,
        SelectableCapabilities,
        VoltageAmplifierCapabilities,
        VoltageAmplifierProtocol,
        VoltageGainCapabilities,
    )


class FakeAmplifier(VoltageAmplifierProtocol):
    """In-memory driver that enforces its declared capability set."""

    capabilities: ClassVar[VoltageAmplifierCapabilities]

    def __init__(
        self,
        capabilities: VoltageAmplifierCapabilities,
        initial_state: AmplifierState,
        *,
        statistics: ChannelStatistics | None = None,
        capture: Capture | None = None,
    ) -> None:
        self.capabilities = capabilities
        self._state = initial_state
        self._statistics = statistics
        self._capture = capture
        self._connected = False

    async def connect(self) -> None:
        """Mark the fake transport as open."""
        self._connected = True

    async def close(self) -> None:
        """Mark the fake transport as closed."""
        self._connected = False

    async def read_data(self) -> AmplifierState:
        """Return the current snapshot, refusing to read while disconnected."""
        if not self._connected:
            raise RuntimeError("amplifier is not connected")
        return self._state

    @staticmethod
    def _require_read(access: Access, name: str) -> None:
        if not access.read:
            raise NotImplementedError(f"{name}() is unavailable: no read capability")

    @staticmethod
    def _require_write(access: Access, name: str) -> None:
        if not access.write:
            raise NotImplementedError(f"{name}() is unavailable: no write capability")

    def _filter_access(self) -> Access:
        filters = self.capabilities.filters
        return Access(
            read=filters.enabled.read or filters.cutoff.read,
            write=filters.enabled.write or filters.cutoff.write,
        )

    async def get_gain(self) -> float:
        self._require_read(self.capabilities.gain.gain.access, "get_gain")
        return self._state.gain

    async def set_gain(self, gain: float) -> None:
        self._require_write(self.capabilities.gain.gain.access, "set_gain")
        self._state = replace(self._state, gain=gain)

    async def get_filter(self, filter_type: FilterType) -> FilterSetting:
        self._require_read(self._filter_access(), "get_filter")
        for setting in self._state.filters:
            if setting.type == filter_type:
                return setting
        return FilterSetting(type=filter_type)

    async def set_filter(self, setting: FilterSetting) -> None:
        self._require_write(self._filter_access(), "set_filter")
        filters = self._state.filters
        if any(existing.type == setting.type for existing in filters):
            filters = tuple(
                setting if existing.type == setting.type else existing
                for existing in filters
            )
        else:
            filters = (*filters, setting)
        self._state = replace(self._state, filters=filters)

    async def get_inverted(self) -> bool:
        self._require_read(self.capabilities.invert_output, "get_inverted")
        return self._state.inverted

    async def set_inverted(self, inverted: bool) -> None:
        self._require_write(self.capabilities.invert_output, "set_inverted")
        self._state = replace(self._state, inverted=inverted)

    async def get_statistics(self) -> ChannelStatistics:
        self._require_read(self.capabilities.measurement.statistics, "get_statistics")
        if self._statistics is None:
            return ChannelStatistics()
        return self._statistics

    async def capture(self) -> Capture:
        self._require_write(self.capabilities.measurement.capture, "capture")
        if self._capture is None:
            return Capture()
        return self._capture


def full_amplifier() -> FakeAmplifier:
    """An amplifier that advertises and reports every modelled feature."""
    read_write = Access(read=True, write=True)
    capabilities = VoltageAmplifierCapabilities(
        gain=VoltageGainCapabilities(
            gain=SelectableCapabilities(
                access=read_write,
                options=(1.0, 10.0, 100.0),
            ),
            min_gain=1.0,
            max_gain=100.0,
            step=0.1,
            default_gain=1.0,
        ),
        input_voltage=InputVoltageCapabilities(min_v=-10.0, max_v=10.0),
        output_voltage=OutputVoltageCapabilities(min_v=-10.0, max_v=10.0),
        filters=FilterCapabilities(
            types=(FilterType.LOW_PASS, FilterType.HIGH_PASS),
            enabled=read_write,
            cutoff=read_write,
        ),
        output_overload=IndicationCapabilities(read=True),
        invert_output=read_write,
        measurement=MeasurementCapabilities(
            statistics=Access(read=True),
            capture=read_write,
            max_capture_points=256,
            max_capture_averaging=4000,
        ),
    )
    state = AmplifierState(
        input_voltage_v=1.0,
        output_voltage_v=10.0,
        output_current_a=1.0e-3,
        gain=10.0,
        filters=(
            FilterSetting(
                type=FilterType.LOW_PASS,
                enabled=True,
                high_cutoff_hz=1e3,
            ),
        ),
        output_overload=False,
        inverted=False,
    )
    statistics = ChannelStatistics(
        channel=1,
        mean_v=1.0,
        min_v=0.5,
        max_v=1.5,
        rms_v=1.02,
        ac_rms_v=0.2,
        peak_to_peak_v=1.0,
        rail_samples=0,
        window_samples=781,
    )
    capture = Capture(
        channel=1,
        points=(0.0,) * 256,
        averaging=781,
        sample_rate_hz=100_000.0,
        block_number=1,
    )
    return FakeAmplifier(capabilities, state, statistics=statistics, capture=capture)


def read_only_gain_amplifier() -> FakeAmplifier:
    """An amplifier whose only feature is a continuous, read-only gain."""
    capabilities = VoltageAmplifierCapabilities(
        gain=VoltageGainCapabilities(
            gain=SelectableCapabilities(access=Access(read=True)),
            min_gain=1.0,
            max_gain=100.0,
        )
    )
    return FakeAmplifier(capabilities, AmplifierState(gain=10.0))


def minimal_amplifier() -> FakeAmplifier:
    """An amplifier with no advertised feature beyond the core contract."""
    return FakeAmplifier(VoltageAmplifierCapabilities(), AmplifierState())
