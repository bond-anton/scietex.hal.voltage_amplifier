"""Register names for the generic Modbus voltage amplifier driver.

The enum names what the driver has to read and write; it carries no wire
addresses. Only :attr:`RegisterSpec.address` and :attr:`RegisterSpec.function`
hold those, and only the device manual may fill them in. Until they are
transcribed, every entry of :data:`DEVICE_MAP` is the neutral default and the
driver refuses to touch the bus.

The map is keyed by every :class:`Register`, built in one comprehension, so a
new member cannot be left without a specification. A boolean register is
written as 0 or 1 and read as ``raw != 0``; the manual must confirm that
encoding before its spec is filled in, because an inverted or bit-packed flag
would make both directions wrong.

Classes:
    Register: The registers the driver needs, grouped by capability.
    RegisterSpec: One register's wire address, function code and scaling.

Constants:
    DEVICE_MAP: The specification of every register, all unmapped by default.
    STATUS_BITS: Status-word bit masks, unknown pending the manual.
    FILTER_TYPE_CODES: Filter type codes, unknown pending the manual.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, auto

from ...base import FilterType


class Register(IntEnum):
    """Registers the driver needs, in capability order.

    The value is an internal key, never a wire address: two registers may share
    an address on the device, and the map, not the enum, carries that.
    """

    INPUT_VOLTAGE = auto()
    OUTPUT_VOLTAGE = auto()
    OUTPUT_CURRENT = auto()

    GAIN = auto()

    FILTER_ENABLE = auto()
    FILTER_TYPE = auto()
    FILTER_LOW_CUTOFF = auto()
    FILTER_HIGH_CUTOFF = auto()

    STATUS = auto()

    INVERT = auto()


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    """Wire-level description of one register.

    Attributes:
        address: Modbus register address, or ``None`` until the manual is read.
        function: Modbus function code that reads the register, or ``None``
            until the manual is read. Writes use the Modbus write function the
            shared client selects, so the code here only has to name the table
            the value is read from.
        scale: Multiplier applied to a raw value to reach engineering units.
        offset: Addend applied after ``scale``.
        signed: Whether the raw value is two's-complement.
        word_count: Number of 16-bit registers the value occupies.
    """

    address: int | None = None
    function: int | None = None
    scale: float = 1.0
    offset: float = 0.0
    signed: bool = False
    word_count: int = 1

    @property
    def mapped(self) -> bool:
        """Whether both the address and the function code are known."""
        return self.address is not None and self.function is not None


DEVICE_MAP: Mapping[Register, RegisterSpec] = {
    register: RegisterSpec() for register in Register
}

# Bit masks are unknown until the manual names the status word's bits. The
# driver reports the indication as ``None`` while this stays empty.
STATUS_BITS: Mapping[str, int] = {}

# Enumeration codes are unknown until the manual names them; a filter whose
# code is absent from this table is reported as unknown rather than guessed.
FILTER_TYPE_CODES: Mapping[FilterType, int] = {}
