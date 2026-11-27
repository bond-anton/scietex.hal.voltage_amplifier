"""Device identity decoded from the EBIC_ADC input registers.

The firmware reports the identity as one block of read-only input registers:
ten vendor characters, twenty product characters, three version words, two
serial-number words and the check constant. This module decodes that block
without touching the bus.

Classes:
    DeviceIdentity: Vendor, product, firmware version and serial number.

Functions:
    decode_identity: Decode the identity block.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .constants import IDENTITY_CHECK, InputRegister
from .decode import u32
from .errors import UnexpectedResponse

IDENTITY_REGISTER_COUNT = InputRegister.CHECK + 1
"""Number of input registers in the identity block, addresses 0-35.

The check register closes the block: the identity can only be trusted when
register 35 is read and equals :data:`IDENTITY_CHECK`.
"""

_STRING_TERMINATORS = " \x00"
"""Characters the firmware pads identity strings with."""


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """The identity the firmware reports through input registers 0-35.

    Attributes:
        vendor: Vendor string, ``SCIETEX.RU`` on a factory device.
        product: Product string, ``EBIC_ADC`` on a factory device.
        version_major: Firmware major version.
        version_minor: Firmware minor version.
        version_patch: Firmware patch version.
        serial_number: 32-bit serial number built from the high and low words.
    """

    vendor: str
    product: str
    version_major: int
    version_minor: int
    version_patch: int
    serial_number: int


def decode_identity(registers: Sequence[int]) -> DeviceIdentity:
    """Decode the input-register block into a :class:`DeviceIdentity`.

    Args:
        registers: Input-register words starting at address 0. At least
            :data:`IDENTITY_REGISTER_COUNT` words are required.

    Returns:
        DeviceIdentity: The decoded identity. Trailing spaces and NUL characters
        the firmware pads the strings with are removed.

    Raises:
        ValueError: If fewer than :data:`IDENTITY_REGISTER_COUNT` words are given.
        UnexpectedResponse: If the check register is not :data:`IDENTITY_CHECK`,
            so the block cannot be trusted as an identity.
    """
    if len(registers) < IDENTITY_REGISTER_COUNT:
        raise ValueError(
            f"identity needs {IDENTITY_REGISTER_COUNT} input registers, "
            f"got {len(registers)}"
        )

    check = registers[InputRegister.CHECK]
    if check != IDENTITY_CHECK:
        raise UnexpectedResponse(
            "identity",
            f"expected check {IDENTITY_CHECK}, got {check}",
        )

    return DeviceIdentity(
        vendor=_decode_string(
            registers[InputRegister.VENDOR_START : InputRegister.PRODUCT_START]
        ),
        product=_decode_string(
            registers[InputRegister.PRODUCT_START : InputRegister.VERSION_MAJOR]
        ),
        version_major=registers[InputRegister.VERSION_MAJOR],
        version_minor=registers[InputRegister.VERSION_MINOR],
        version_patch=registers[InputRegister.VERSION_PATCH],
        serial_number=u32(
            registers[InputRegister.SERIAL_HIGH],
            registers[InputRegister.SERIAL_LOW],
        ),
    )


def _decode_string(registers: Sequence[int]) -> str:
    """Decode one character per register and strip the firmware's padding."""
    return "".join(chr(word) for word in registers).rstrip(_STRING_TERMINATORS)
