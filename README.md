# scietex.hal.voltage_amplifier

A Python library for voltage amplifiers: devices that take an input voltage,
scale it by a selectable gain, and optionally filter the signal and invert the
output polarity.

The package is split in two layers. The capability model and the driver contract
in `scietex.hal.voltage_amplifier.base` are complete and usable now: they
describe what an amplifier can do, what it currently reads, and the asynchronous
methods a driver implements. The vendor-neutral Modbus driver in
`scietex.hal.voltage_amplifier.generic.modbus` is a skeleton: its structure, error
handling and capability mapping are in place, but the device's register map has
not been transcribed, so every register access raises `RegisterMapIncomplete`
before any bus traffic starts. The driver is therefore not advertised through the
`scietex.hal.voltage_amplifier.drivers` entry-point group either.

## Status

| Layer | State |
| --- | --- |
| Capability model (`base.capabilities`) | Complete |
| State snapshot (`base.data`) | Complete |
| Driver contract (`base.protocol`) | Complete |
| Driver registry (`registry`) | Complete, no driver advertised yet |
| Generic Modbus driver (`generic.modbus`) | Skeleton: register map missing |

## Install

```bash
pip install scietex.hal.voltage_amplifier
```

The package is not on PyPI yet; install from a clone in the meantime:

```bash
git clone https://github.com/bond-anton/scietex.hal.voltage_amplifier.git
cd scietex.hal.voltage_amplifier
pip install .
```

Requirements:

- Python 3.10 or higher.
- `msgspec` 0.20 for the state snapshot.
- `scietex.hal.serial` 2.5 or newer for the asynchronous RS485 transport.
- `pymodbus` 3.15 for the Modbus request types.

## Usage

Every driver satisfies `VoltageAmplifierProtocol`: `connect`, `close` and
`read_data` are mandatory coroutines, and every optional control method is a
coroutine too. The `supports_*` and `can_*` properties are synchronous and read
straight from `capabilities`, so a caller rejects an unsupported command before
dispatching it.

```python
import asyncio

from scietex.hal.voltage_amplifier.base import VoltageAmplifierProtocol


async def main(amp: VoltageAmplifierProtocol) -> None:
    """Read one snapshot and re-apply a gain the device accepts."""
    await amp.connect()
    try:
        state = await amp.read_data()
        print(state.input_voltage_v, state.output_voltage_v, state.output_current_a)
        await apply_gain(amp, state.gain)
    finally:
        await amp.close()


async def apply_gain(amp: VoltageAmplifierProtocol, gain: float | None) -> None:
    if gain is None:
        return
    if amp.can_set_gain and amp.capabilities.gain.accepts(gain) is True:
        await amp.set_gain(gain)


asyncio.run(main(amplifier))
```

`amplifier` is a driver instance. The repository ships no working hardware
driver yet, so the object is supplied by the application, by a subclass that
implements the optional methods, or by a test double.

The filter and polarity controls follow the same two-step guard: ask the
capability property, then call the coroutine.

```python
from scietex.hal.voltage_amplifier.base import FilterSetting, FilterType

if amp.can_set_filter:
    await amp.set_filter(FilterSetting(type=FilterType.LOW_PASS, enabled=True))

if amp.can_set_invert_output:
    await amp.set_inverted(True)
```

Where an operation is advertised, the capability set answers whether it is
writable; where a value is available, the snapshot carries it. The two are
separate questions: a feature that exists but is switched off is truthy in
`capabilities` and `False` in the state.

```python
capabilities = amp.capabilities
limits = capabilities.output_voltage
print(limits.min_v, limits.max_v)  # static swing limits, or None while unknown

state = await amp.read_data()
if state.gain is not None:
    headroom = capabilities.max_input_voltage_v(state.gain)
    swing = capabilities.max_output_swing_v()
    print(headroom, swing)
```

`max_output_swing_v` returns the smaller of `|min_v|` and `|max_v|`, or `None`
when either limit is unknown. `max_input_voltage_v` divides that swing by the
gain and caps the result with the static input range; it raises `ValueError` for
a zero or negative gain.

## Capabilities

`VoltageAmplifierCapabilities` groups the control surface by function, not by
method name, and every group defaults to "unsupported". A driver declares the
whole set as a class attribute, so it is known before the instance connects.

| Group | Capability fields | Snapshot fields | Support query | Write query |
| --- | --- | --- | --- | --- |
| Gain | `gain.gain`, `gain.min_gain`, `gain.max_gain`, `gain.step`, `gain.default_gain` | `gain` | `supports_gain` | `can_set_gain` |
| Input voltage | `input_voltage.min_v`, `input_voltage.max_v` | `input_voltage_v` | static, read directly | not writable |
| Output voltage | `output_voltage.min_v`, `output_voltage.max_v` | `output_voltage_v` | static, read directly | not writable |
| Filters | `filters.types`, `filters.enabled`, `filters.cutoff` | `filters` | `supports_filters` | `can_set_filter` |
| Output overload | `output_overload.read` | `output_overload` | `supports_output_overload` | not writable |
| Invert output | `invert_output` | `inverted` | `supports_invert_output` | `can_set_invert_output` |

The input range and the output swing are static geometry rather than controllable
features, so they have no `supports_*` property: `capabilities.input_voltage` and
`capabilities.output_voltage` are read directly and hold `min_v` and `max_v`,
while the voltages the device currently reports are `state.input_voltage_v` and
`state.output_voltage_v`.

The output-overload indication is read only through `read_data()`; there is no
dedicated accessor. `input_voltage_v`, `output_voltage_v` and `output_current_a`
are always present in the snapshot and are not gated by a capability.

Some rules the model enforces:

- `SelectableCapabilities.options` is in device-native units and in the device's
  own order. An empty tuple means the device does not enumerate the domain, which
  is typical of a continuous setting such as a gain or a cutoff frequency; it
  does not mean no value is accepted.
- `Access(read=..., write=...)` separates reading from writing, and
  `bool(access)` is true when either direction is supported.
- The gain is dimensionless and expressed in V/V: the output voltage divided by
  the input voltage. A device may enumerate its gains in `gain.gain.options`,
  describe them as a bounded range in `gain.min_gain` and `gain.max_gain`, or do
  neither, in which case the domain is unknown rather than empty. `gain.step`
  documents the smallest increment the device resolves; `accepts` never rounds.
- `gain.default_gain` is the gain the device applies at power-on or reset, or
  `None` when the driver does not know it. It is never assumed to be unity.
- `FilterType` covers `LOW_PASS`, `HIGH_PASS` and `BAND_PASS`; a filter entry is
  a `FilterSetting` with `enabled` and the cutoff corners in Hz that apply.

## Errors

- `NotImplementedError` from an optional method means the driver does not
  implement that capability. Consult `capabilities` and take another path; do not
  treat it as a transport failure.
- A `None` field in `AmplifierState` means the driver cannot report that value.
  It is not `0.0` and not `False`, so a caller must not substitute either. An
  unreported gain is unknown, not unity; an unreadable overload flag is unknown,
  not clear.
- `RegisterMapIncomplete` (a `RuntimeError`) means the register an operation needs
  has no address or function code in `DEVICE_MAP`. The message names the register
  and the file to fill in.
- `UnexpectedResponse` (an `OSError`) means a response cannot belong to the
  register it answers, such as a wrong register count.
- `ValueError` comes from `max_input_voltage_v` for a non-positive gain, and from
  `resolve_driver` for an unknown driver name; the message lists the available
  names.

## The Modbus driver

`GenericModbusVoltageAmplifier` is a `scietex.hal.serial.RS485Client` subclass, so
the shared client opens the port once per operation and `connect()` has nothing to
open. Its capability set is empty and `DEVICE_MAP`, `STATUS_BITS` and
`FILTER_TYPE_CODES` in `scietex/hal/voltage_amplifier/generic/modbus/registers.py`
carry no device data yet.

```python
from scietex.hal.voltage_amplifier.generic.modbus import (
    GenericModbusVoltageAmplifier,
    RegisterMapIncomplete,
)
from scietex.hal.serial import ModbusSerialConnectionConfig

serial_config = ModbusSerialConnectionConfig(port="/dev/ttyUSB0", framer="RTU")
amp = GenericModbusVoltageAmplifier(serial_config, address=1)

try:
    await amp.read_data()
except RegisterMapIncomplete as error:
    print(error)
```

`read_data()` resolves its three mandatory registers before the first read, so an
unmapped device fails without partial traffic. Transcribing the register map —
and filling in the capability set that the map implies — is the change that makes
the driver operate.

### Registers

`Register` names the ten registers the driver needs, in capability order. No
address or function code is assigned yet: every entry of `DEVICE_MAP` is the
neutral default, so every access raises `RegisterMapIncomplete` until the manual
is transcribed.

| Member | Purpose |
| --- | --- |
| `INPUT_VOLTAGE` | Input voltage reading |
| `OUTPUT_VOLTAGE` | Output voltage reading |
| `OUTPUT_CURRENT` | Output current reading |
| `GAIN` | Voltage gain setting and reading |
| `FILTER_ENABLE` | Filter on/off setting |
| `FILTER_TYPE` | Filter response selection |
| `FILTER_LOW_CUTOFF` | Lower cutoff frequency |
| `FILTER_HIGH_CUTOFF` | Upper cutoff frequency |
| `STATUS` | Status word carrying the overload flag |
| `INVERT` | Output-polarity inversion setting |

`STATUS_BITS` and `FILTER_TYPE_CODES` are empty as well: the driver reports the
overload as `None` and an unrecognised filter code as an unknown entry rather than
guessing a bit mask or a code.

Because the driver has no entry point, `registry.discover_drivers()` returns an
empty mapping and `registry.resolve_driver("...")` raises `ValueError`. Until the
device is identified the driver is reachable by direct import only.

## Testing

```bash
pytest tests -q
```

The suite drives in-memory amplifiers and asserts that the Modbus skeleton keeps
the port closed; it needs no hardware and never opens a serial port.

Other checks:

```bash
tox -e format,lint,type
```

`format` and `lint` run `ruff`, `type` runs `ty` over `src`. Continuous
integration runs the same checks plus a distribution build; its test matrix
covers Python 3.10, 3.12 and 3.14.

## License

This project is licensed under the **MIT License** - see the LICENSE file for
details.
