"""
Base voltage amplifier subpackage.

Holds the transport-agnostic pieces every driver shares: the capability model
that advertises what an amplifier can do, the state snapshot it reports, and the
driver contract concrete protocols implement.

Classes:
    Access: Whether a setting can be read and whether it can be written.
    AmplifierState: A snapshot of the values an amplifier reports.
    Capture: One buffered waveform acquisition.
    ChannelStatistics: Statistics over the device-side measurement window.
    FilterCapabilities: Signal-filter surface.
    FilterSetting: One filter in the amplifier's signal path.
    FilterType: The filter responses an amplifier can apply.
    IndicationCapabilities: A read-only status flag.
    InputVoltageCapabilities: Static input voltage range limits.
    MeasurementCapabilities: Statistics and waveform-capture surface.
    OutputVoltageCapabilities: Static output voltage swing limits.
    SelectableCapabilities: An access plus the values it accepts.
    VoltageAmplifierCapabilities: The complete capability set for one amplifier.
    VoltageAmplifierProtocol: The contract every voltage amplifier driver
        implements.
    VoltageGainCapabilities: Gain selection with the bounds it accepts.

Modules:
    capabilities: Defines the capability model.
    data: Defines the state snapshot, the filter setting and the measurement
        values.
    protocol: Defines the `VoltageAmplifierProtocol` driver contract.
"""

from .capabilities import (
    Access,
    FilterCapabilities,
    FilterType,
    IndicationCapabilities,
    InputVoltageCapabilities,
    MeasurementCapabilities,
    OutputVoltageCapabilities,
    SelectableCapabilities,
    VoltageAmplifierCapabilities,
    VoltageGainCapabilities,
)
from .data import AmplifierState, Capture, ChannelStatistics, FilterSetting
from .protocol import VoltageAmplifierProtocol

__all__ = [
    "Access",
    "AmplifierState",
    "Capture",
    "ChannelStatistics",
    "FilterCapabilities",
    "FilterSetting",
    "FilterType",
    "IndicationCapabilities",
    "InputVoltageCapabilities",
    "MeasurementCapabilities",
    "OutputVoltageCapabilities",
    "SelectableCapabilities",
    "VoltageAmplifierCapabilities",
    "VoltageAmplifierProtocol",
    "VoltageGainCapabilities",
]
