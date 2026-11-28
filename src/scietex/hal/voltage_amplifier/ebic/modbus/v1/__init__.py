"""EBIC_ADC Modbus v1 subpackage.

Modules:
    client: The RS485/RTU driver over the request layer.
    client_tcp: The Modbus/TCP transport over the request layer.
    constants: Wire register, coil, ADC geometry and timing constants.
    decode: Decoders from register words to physical volts.
    errors: Request and capture failures.
    exceptions: Gateway outcomes the TCP transport names.
    identity: Decodes the device identity from input registers.
    requests: The transport-independent request layer.
    settings: Decoded settings, status and capture metadata.

Exports:
    BoosterTuning: Divider, transaction gap and read attempts.
    EBICModbusV1Booster: The EBIC_ADC driver over RS485/RTU.
    EBICModbusV1BoosterTcp: The EBIC_ADC transport over Modbus/TCP.
    BoosterRequests: The transport-independent request layer.
    DeviceIdentity: The vendor, product, version and serial number.
    Capture: One buffered waveform acquisition in physical volts.
    AdcFlags: Bits of the ADC flags register.
    StopReason: Why the last capture stopped.
    CommunicationSettings: Persisted slave address and baud rate.
    AcquisitionSettings: Channel, averaging and rate configuration.
    DeviceStatus: Statistics-block status snapshot.
    CaptureMetadata: What the last capture ran with and why it stopped.
    DEFAULT_DIVIDER_RATIO: External input divider, 0.6 on the EBIC board.
    UnexpectedResponse: A response cannot belong to its request.
    CaptureTimeout: A capture did not finish within its deadline.
    CaptureFailed: A capture stopped without producing a buffer.
    GatewayBusy: The gateway or device was busy; the request is retryable.
    GatewayTargetFailed: The gateway could not reach the device behind it.
    decode_identity: Decode the identity block.
    decode_channel_statistics: Decode one channel's statistics block.
    decode_capture_column: Decode one channel of a capture buffer.
    decode_stop_reason: Decode the capture stop reason.
    pin_volts: Convert an ADC code to pin volts.
    physical_v: Convert an ADC code to physical volts.
    u32: Combine two words into an unsigned 32-bit value.
"""

from ....base import Capture
from .client import BoosterTuning, EBICModbusV1Booster
from .client_tcp import EBICModbusV1BoosterTcp
from .constants import DEFAULT_DIVIDER_RATIO
from .decode import (
    decode_capture_column,
    decode_channel_statistics,
    decode_stop_reason,
    physical_v,
    pin_volts,
    u32,
)
from .errors import CaptureFailed, CaptureTimeout, UnexpectedResponse
from .exceptions import GatewayBusy, GatewayTargetFailed
from .identity import DeviceIdentity, decode_identity
from .requests import BoosterRequests
from .settings import (
    AcquisitionSettings,
    AdcFlags,
    CaptureMetadata,
    CommunicationSettings,
    DeviceStatus,
    StopReason,
)

__all__ = [
    "AcquisitionSettings",
    "AdcFlags",
    "BoosterRequests",
    "BoosterTuning",
    "Capture",
    "CaptureFailed",
    "CaptureMetadata",
    "CaptureTimeout",
    "CommunicationSettings",
    "DEFAULT_DIVIDER_RATIO",
    "DeviceIdentity",
    "DeviceStatus",
    "EBICModbusV1Booster",
    "EBICModbusV1BoosterTcp",
    "GatewayBusy",
    "GatewayTargetFailed",
    "StopReason",
    "UnexpectedResponse",
    "decode_capture_column",
    "decode_channel_statistics",
    "decode_identity",
    "decode_stop_reason",
    "physical_v",
    "pin_volts",
    "u32",
]
