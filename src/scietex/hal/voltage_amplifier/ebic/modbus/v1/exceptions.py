"""Exceptions raised by the EBIC_ADC Modbus/TCP transport.

The exceptions this module adds extend
:class:`scietex.hal.serial.ModbusOperationError`, so a caller catches one base
type whether the booster is reached over RS485 or over a Modbus/TCP gateway.
Two gateway outcomes need names of their own. :class:`GatewayTargetFailed`
(exception code 11) means the gateway answered, but its own link to the booster
timed out: the board may be down, and such a write may still have reached it, so
a blind retry is unsafe. :class:`GatewayBusy` (exception code 6) means the bus is
contended: the request was not served, the board is not necessarily dead, and
the same request may succeed shortly.

Classes:
    GatewayBusy: The gateway or device was busy; the request is retryable.
    GatewayTargetFailed: The gateway could not reach the device behind it.
"""

from scietex.hal.serial import ModbusOperationError

GATEWAY_BUSY = 0x06
"""Modbus exception code 6: server device busy."""

GATEWAY_TARGET_FAILED = 0x0B
"""Modbus exception code 11: gateway target device failed to respond."""


class GatewayBusy(ModbusOperationError):
    """The gateway or the device behind it was busy.

    Code 6 means the request was refused because the bus is contended, not
    because the board is unreachable. The same request may succeed shortly, so
    a service should retry it rather than mark the board unhealthy. A device
    that reports the code for its own busy state is classified the same way,
    because the retryable reading is correct in both cases.
    """


class GatewayTargetFailed(ModbusOperationError):
    """The gateway could not reach the addressed device.

    A plain :class:`ModbusOperationError` means the device rejected the request
    or the link failed before a response arrived. Code 11 is narrower: the
    gateway accepted the request, then failed to reach the target, so the
    outcome on the device side is unknown and a write must not be retried
    blindly. The request layer never retries writes, so the caller decides.
    """
