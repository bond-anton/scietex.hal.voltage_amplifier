"""Generic Modbus driver for the voltage amplifier.

The driver is reachable by direct import until the device is identified and the
register map is transcribed; it is deliberately not advertised through the
driver entry points yet.

Exports:
    GenericModbusVoltageAmplifier: The vendor-neutral Modbus driver.
    RegisterMapIncomplete: A register the operation needs is not mapped.
    UnexpectedResponse: A response cannot belong to the register it answers.
    Register: The registers the driver needs.
    RegisterSpec: One register's wire address, function code and scaling.
    DEVICE_MAP: The specification of every register.
    STATUS_BITS: Status-word bit masks.
    FILTER_TYPE_CODES: Filter type enumeration codes.
"""

from .client import GenericModbusVoltageAmplifier
from .errors import RegisterMapIncomplete, UnexpectedResponse
from .registers import (
    DEVICE_MAP,
    FILTER_TYPE_CODES,
    STATUS_BITS,
    Register,
    RegisterSpec,
)

__all__ = [
    "DEVICE_MAP",
    "FILTER_TYPE_CODES",
    "STATUS_BITS",
    "GenericModbusVoltageAmplifier",
    "Register",
    "RegisterMapIncomplete",
    "RegisterSpec",
    "UnexpectedResponse",
]
