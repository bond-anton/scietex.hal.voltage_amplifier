"""Tests for decoding the EBIC_ADC identity block.

The identity is a canned register block: a caller can only trust it when the
check register closes the block, so a short read and a wrong check must both be
rejected rather than decoded.
"""

import pytest

try:
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        IDENTITY_CHECK,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from src.scietex.hal.voltage_amplifier.ebic.modbus.v1.identity import (
        IDENTITY_REGISTER_COUNT,
        DeviceIdentity,
        decode_identity,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.constants import (
        IDENTITY_CHECK,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.errors import (
        UnexpectedResponse,
    )
    from scietex.hal.voltage_amplifier.ebic.modbus.v1.identity import (
        IDENTITY_REGISTER_COUNT,
        DeviceIdentity,
        decode_identity,
    )


def _identity_block(
    *,
    check: int = IDENTITY_CHECK,
    serial_high: int = 0x1234,
    serial_low: int = 0x5678,
) -> list[int]:
    """Build the 36-register identity block of a factory device."""
    registers = [0] * IDENTITY_REGISTER_COUNT
    registers[0:10] = [ord(character) for character in "SCIETEX.RU"]
    registers[10:30] = [ord(character) for character in "EBIC_ADC".ljust(20)]
    registers[30:33] = [2, 3, 2]
    registers[33] = serial_high
    registers[34] = serial_low
    registers[35] = check
    return registers


def test_identity_decodes_vendor_product_version_and_serial():
    """The canned block decodes to the factory identity, word for word."""
    identity = decode_identity(_identity_block())

    assert identity == DeviceIdentity(
        vendor="SCIETEX.RU",
        product="EBIC_ADC",
        version_major=2,
        version_minor=3,
        version_patch=2,
        serial_number=0x12345678,
    )


def test_trailing_product_padding_is_stripped():
    """The twenty product registers are space-padded; the padding is not data."""
    identity = decode_identity(_identity_block())

    assert identity.product == "EBIC_ADC"
    assert not identity.product.endswith(" ")


def test_serial_number_is_built_high_word_first():
    """The serial number is a 32-bit value, high word in register 33."""
    identity = decode_identity(_identity_block(serial_high=0x0001, serial_low=0x0002))

    assert identity.serial_number == 0x00010002


def test_short_block_is_rejected_before_decoding():
    """A block that stops before the check register cannot be trusted."""
    with pytest.raises(ValueError, match="identity needs"):
        decode_identity(_identity_block()[:-1])


def test_wrong_check_register_raises_unexpected_response():
    """A block whose check word is not 7 is not an EBIC_ADC identity."""
    registers = _identity_block(check=IDENTITY_CHECK + 1)

    with pytest.raises(UnexpectedResponse, match="check"):
        decode_identity(registers)
