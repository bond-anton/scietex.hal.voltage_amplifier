"""Wire constants for the EBIC_ADC Modbus RTU interface.

The values transcribe firmware 2.3.2: the input and holding register maps, the
single relay coil, the ADC geometry, the rate-mode and baud-rate tables, and the
timing bounds the transport enforces. The device name the firmware reports is
``EBIC_ADC``; the board itself is the EBIC Boost Control.

Classes:
    InputRegister: Read-only input-register addresses (FC04).
    HoldingRegister: Read/write holding-register addresses (FC03/FC06).

Constants:
    RELAY_COIL: The only coil; drives relay 0, which selects inversion.
    DEVICE_NAME: Product string in input registers 10-29.
    DEFAULT_SLAVE_ADDRESS: Address used on an empty EEPROM.
    ADC_REFERENCE_MV, ADC_REFERENCE_V, ADC_FULL_SCALE_CODE: 12-bit ADC geometry.
    SENTINEL_UNAVAILABLE: Word meaning "value unavailable", converted to ``None``.
    DEFAULT_DIVIDER_RATIO: External input divider, shared by both channels.
    RATE_MODE_SAMPLE_RATES: Rate-mode value to samples per second.
    BAUD_RATES: Baud-index value to baud rate.
    MIN_TRANSACTION_GAP_S: Lowest gap between consecutive requests (t3.5).
"""

from enum import IntEnum


class InputRegister(IntEnum):
    """Input-register addresses (FC04 read only)."""

    VENDOR_START = 0
    """First vendor-string register; ten registers, one character each."""

    PRODUCT_START = 10
    """First product-string register; twenty registers, one character each."""

    VERSION_MAJOR = 30
    VERSION_MINOR = 31
    VERSION_PATCH = 32

    SERIAL_HIGH = 33
    """High word of the 32-bit serial number."""

    SERIAL_LOW = 34
    """Low word of the 32-bit serial number."""

    CHECK = 35
    """Identity check constant, always :data:`IDENTITY_CHECK`."""

    RELAY_STATE = 36
    """Relay 0 state as the firmware drives it."""

    BUTTON = 37

    ADC_FLAGS = 38
    """Bit field decoded by ``settings.AdcFlags``."""

    STATS_BLOCK_HIGH = 39
    STATS_BLOCK_LOW = 40

    ADC_RATE_HIGH = 41
    ADC_RATE_LOW = 42

    WINDOW_SIZE = 43
    """Number of samples in the statistics window."""

    ERROR_COUNT_HIGH = 44
    ERROR_COUNT_LOW = 45

    PAUSE_COUNT_HIGH = 46
    PAUSE_COUNT_LOW = 47

    STATS_AGE_HIGH = 48
    STATS_AGE_LOW = 49

    ADC_VREF_MV = 50
    """Measured ADC reference in mV."""

    PA0_STATS_START = 52
    """First of ten statistics registers for channel 0."""

    PA1_STATS_START = 62
    """First of ten statistics registers for channel 1."""

    CAPTURE_BLOCK_HIGH = 72
    CAPTURE_BLOCK_LOW = 73

    LAST_MEAN_PA0_MV = 74
    LAST_MEAN_PA1_MV = 75

    OVERLOAD = 76

    SELECTED_CHANNEL = 77
    """Channel the acquisition is configured for."""

    CAPTURE_CHANNEL = 78
    """Channel the last capture ran on."""

    CAPTURE_AVERAGING = 79
    """Averaging depth the last capture ran with."""

    STOP_REASON = 80
    """Why the last capture stopped; decoded by ``settings.StopReason``."""

    HAL_ADC_ERROR = 81
    HAL_DMA_ERROR = 82

    CAPTURE_START = 128
    """First of 512 captured sample registers, two channels interleaved."""

    CAPTURE_END = 639
    """Last captured sample register."""


class HoldingRegister(IntEnum):
    """Holding-register addresses (FC03 read, FC06 write)."""

    SLAVE_ADDRESS = 0
    """Slave address, 1-247, persisted to EEPROM and applied after a restart."""

    BAUD_INDEX = 1
    """Baud index, 0-5, persisted to EEPROM and applied after a restart."""

    CAPTURE_COMMAND = 2
    """Write 1 to start a capture."""

    CHANNEL = 3
    """Acquisition channel: 0 = PA0, 1 = PA1."""

    AVERAGING = 4
    """Averaging depth, 1-4000."""

    RATE_MODE = 5
    """Sample-rate mode, 0-2; see :data:`RATE_MODE_SAMPLE_RATES`."""


RELAY_COIL = 0
"""The only coil. Logic 0 selects inversion, logic 1 passes the signal."""

DEVICE_NAME = "EBIC_ADC"
"""Product string in input registers 10-29, space-padded by the firmware."""

DEFAULT_SLAVE_ADDRESS = 2
"""Slave address the firmware uses on an empty EEPROM."""

SLAVE_ADDRESS_MIN = 1
"""Lowest accepted Modbus slave address."""

SLAVE_ADDRESS_MAX = 247
"""Highest accepted Modbus slave address."""

DEFAULT_BAUD_INDEX = 2
"""Baud index that selects the 19200 baud power-on default."""

IDENTITY_CHECK = 7
"""Value input register 35 must hold for the identity to be trusted."""

ADC_REFERENCE_MV = 3300
"""ADC reference voltage in mV."""

ADC_REFERENCE_V = 3.3
"""ADC reference voltage in V."""

ADC_FULL_SCALE_CODE = 4095
"""Largest code a 12-bit ADC conversion returns."""

STATS_WINDOW_SAMPLES = 256
"""Number of samples the statistics window averages."""

CAPTURE_POINTS = 256
"""Number of samples one capture returns per channel."""

CAPTURE_REGISTER_COUNT = 512
"""Number of input registers the capture block occupies, two per point."""

MAX_REGISTERS_PER_READ = 61
"""Most registers one read request may ask for."""

SENTINEL_UNAVAILABLE = 0xFFFF
"""Word that means "value unavailable"; decoded as ``None``, never scaled."""

DEFAULT_DIVIDER_RATIO = 0.6
"""External input divider, shared by both channels: 5 V at the input is 3 V at the pin."""

MAX_CAPTURE_AVERAGING = 4000
"""Largest averaging depth the firmware accepts."""

RATE_MODE_SAMPLE_RATES: dict[int, int] = {
    0: 100_000,
    1: 500_000,
    2: 1_250_000,
}
"""Rate-mode value to ADC samples per second."""

BAUD_RATES: dict[int, int] = {
    0: 9600,
    1: 14400,
    2: 19200,
    3: 38400,
    4: 57600,
    5: 115200,
}
"""Baud index to baud rate; index 2 is the power-on default."""

EEPROM_WRITE_TIMEOUT_S = 5.0
"""Time to allow the firmware to persist a communication setting."""

REBOOT_DELAY_S = 0.6
"""Delay between acknowledging an address or baud change and restarting."""

MIN_TRANSACTION_GAP_S = 0.006
"""Modbus RTU t3.5 gap that separates two transactions at 19200 baud."""

CAPTURE_POLL_INTERVAL_S = 0.05
"""Delay between polls while waiting for a capture to finish."""

CAPTURE_TIMEOUT_MARGIN_S = 0.5
"""Extra time granted to a capture beyond its nominal duration."""

READ_ATTEMPTS = 3
"""Total attempts a read makes before the transport error propagates."""
