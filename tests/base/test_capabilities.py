"""Tests for the capability model."""

from dataclasses import FrozenInstanceError, fields

import pytest

try:
    from src.scietex.hal.voltage_amplifier.base.capabilities import (
        Access,
        FilterCapabilities,
        FilterType,
        IndicationCapabilities,
        InputVoltageCapabilities,
        OutputVoltageCapabilities,
        SelectableCapabilities,
        VoltageAmplifierCapabilities,
        VoltageGainCapabilities,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base.capabilities import (
        Access,
        FilterCapabilities,
        FilterType,
        IndicationCapabilities,
        InputVoltageCapabilities,
        OutputVoltageCapabilities,
        SelectableCapabilities,
        VoltageAmplifierCapabilities,
        VoltageGainCapabilities,
    )


def _with_output_voltage(min_v, max_v):
    """Return capabilities carrying only an output voltage range."""
    return VoltageAmplifierCapabilities(
        output_voltage=OutputVoltageCapabilities(min_v=min_v, max_v=max_v)
    )


def test_defaults_advertise_nothing():
    """Every capability group defaults to unsupported with no known limits."""
    capabilities = VoltageAmplifierCapabilities()

    assert capabilities.gain.gain.access == Access(False, False)
    assert capabilities.gain.gain.options == ()
    assert capabilities.gain.min_gain is None
    assert capabilities.gain.max_gain is None
    assert capabilities.gain.step is None
    assert capabilities.gain.default_gain is None
    assert capabilities.input_voltage.min_v is None
    assert capabilities.input_voltage.max_v is None
    assert capabilities.output_voltage.min_v is None
    assert capabilities.output_voltage.max_v is None
    assert capabilities.filters.types == ()
    assert capabilities.filters.enabled == Access(False, False)
    assert capabilities.filters.cutoff == Access(False, False)
    assert capabilities.output_overload.read is False
    assert capabilities.invert_output == Access(False, False)


def test_default_gain_capabilities_are_falsy_and_unenumerated():
    """A gain nobody declared is unsupported, has no options and no default."""
    gain = VoltageGainCapabilities()

    assert bool(gain) is False
    assert gain.gain.options == ()
    assert gain.gain.access == Access(False, False)
    assert gain.default_gain is None


def test_gain_is_truthy_once_its_access_is_declared():
    """An enumerated gain list advertises support through its access."""
    gain = VoltageGainCapabilities(
        gain=SelectableCapabilities(
            access=Access(read=True),
            options=(1.0, 10.0, 100.0),
        )
    )

    assert bool(gain) is True


def test_gain_is_truthy_from_a_bound_without_a_selectable_domain():
    """A continuous gain is supported by its bounds alone."""
    assert bool(VoltageGainCapabilities(min_gain=1.0)) is True
    assert bool(VoltageGainCapabilities(max_gain=100.0)) is True


def test_accepts_checks_membership_when_gains_are_enumerated():
    """Enumerated options accept exactly the listed values."""
    gain = VoltageGainCapabilities(
        gain=SelectableCapabilities(
            access=Access(read=True),
            options=(1.0, 10.0, 100.0),
        )
    )

    assert gain.accepts(10.0) is True
    assert gain.accepts(5.0) is False


@pytest.mark.parametrize(
    ("min_gain", "max_gain", "gain", "expected"),
    [
        (1.0, 100.0, 50.0, True),
        (1.0, 100.0, 0.5, False),
        (1.0, 100.0, 200.0, False),
        (1.0, None, 1e6, True),
        (None, 100.0, 0.01, True),
    ],
    ids=["inside", "below-min", "above-max", "min-only", "max-only"],
)
def test_accepts_uses_bounds_when_only_a_range_is_declared(
    min_gain, max_gain, gain, expected
):
    """An unknown bound leaves that side of the range unbounded."""
    capabilities = VoltageGainCapabilities(min_gain=min_gain, max_gain=max_gain)

    assert capabilities.accepts(gain) is expected


def test_accepts_is_unknown_without_any_domain():
    """An undeclared domain is unknown, which is not an empty domain."""
    assert VoltageGainCapabilities().accepts(10.0) is None


def test_step_does_not_quantise_accepts():
    """The documented increment never rounds a gain inside the bounds."""
    capabilities = VoltageGainCapabilities(min_gain=1.0, max_gain=100.0, step=0.1)

    assert capabilities.accepts(1.05) is True


def test_voltage_limits_default_to_unknown():
    """Undeclared input and output ranges are unknown in both directions."""
    input_limits = InputVoltageCapabilities()
    output_limits = OutputVoltageCapabilities()

    assert input_limits.min_v is None
    assert input_limits.max_v is None
    assert output_limits.min_v is None
    assert output_limits.max_v is None


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        (Access(False, False), False),
        (Access(read=True), True),
        (Access(write=True), True),
    ],
)
def test_access_bool_reflects_either_direction(access, expected):
    """An access is truthy when at least one direction is available."""
    assert bool(access) is expected


def test_selectable_capabilities_bool_tracks_access_not_options():
    """A selectable setting is supported by its access, not by its options."""
    assert bool(SelectableCapabilities()) is False
    assert bool(SelectableCapabilities(options=(1.0,))) is False
    assert bool(SelectableCapabilities(access=Access(read=True))) is True


def test_selectable_capabilities_preserve_options():
    """Options keep their device order and exact values."""
    options = (1e-9, 0.1, 1234.5678)
    capabilities = SelectableCapabilities(access=Access(read=True), options=options)

    assert capabilities.options == options
    assert capabilities.options[0] == 1e-9
    assert capabilities.options[-1] == 1234.5678


@pytest.mark.parametrize(
    "instance",
    [
        Access(read=True, write=True),
        SelectableCapabilities(access=Access(read=True), options=(1.0, 2.0)),
        OutputVoltageCapabilities(min_v=-5.0, max_v=5.0),
        InputVoltageCapabilities(min_v=-5.0, max_v=5.0),
        VoltageGainCapabilities(gain=SelectableCapabilities(access=Access(read=True))),
        FilterCapabilities(types=(FilterType.LOW_PASS,)),
        IndicationCapabilities(read=True),
        VoltageAmplifierCapabilities(),
    ],
    ids=[
        "access",
        "selectable",
        "output_voltage",
        "input_voltage",
        "gain",
        "filters",
        "indication",
        "voltage_amplifier",
    ],
)
def test_capability_dataclasses_are_frozen(instance):
    """A capability value is immutable once constructed."""
    field_name = fields(instance)[0].name

    with pytest.raises(FrozenInstanceError):
        setattr(instance, field_name, None)


def test_filter_type_is_a_string_enum():
    """The filter member value is the wire spelling a driver sends."""
    assert FilterType.LOW_PASS == "low_pass"
    assert isinstance(FilterType.LOW_PASS, str)


def test_max_output_swing_takes_the_smaller_magnitude():
    """An asymmetric range allows only the excursion both rails permit."""
    assert _with_output_voltage(-5.0, 10.0).max_output_swing_v() == 5.0


@pytest.mark.parametrize(
    ("min_v", "max_v"),
    [(None, 10.0), (-5.0, None), (None, None)],
    ids=["no-min", "no-max", "no-limits"],
)
def test_max_output_swing_is_unknown_when_a_limit_is_missing(min_v, max_v):
    """A missing limit makes the symmetric excursion unknown, not unbounded."""
    assert _with_output_voltage(min_v, max_v).max_output_swing_v() is None


def test_max_input_voltage_divides_the_swing_by_the_gain():
    """A 10 V swing at a gain of 2 admits 5 V at the input."""
    capabilities = _with_output_voltage(-10.0, 10.0)

    assert capabilities.max_input_voltage_v(2.0) == 5.0


def test_max_input_voltage_uses_the_tighter_input_limit():
    """A declared input range binds before the swing-referred ceiling."""
    capabilities = VoltageAmplifierCapabilities(
        input_voltage=InputVoltageCapabilities(min_v=-2.0, max_v=2.0),
        output_voltage=OutputVoltageCapabilities(min_v=-10.0, max_v=10.0),
    )

    assert capabilities.max_input_voltage_v(1.0) == 2.0


def test_max_input_voltage_is_unknown_without_any_limit():
    """Without an input range or an output swing no ceiling can be derived."""
    assert VoltageAmplifierCapabilities().max_input_voltage_v(2.0) is None


@pytest.mark.parametrize("gain", [0.0, -1.0])
def test_max_input_voltage_rejects_non_positive_gain(gain):
    """A gain that does not scale a voltage cannot define an input ceiling."""
    with pytest.raises(ValueError, match="gain must be positive"):
        _with_output_voltage(-10.0, 10.0).max_input_voltage_v(gain)


def test_current_amplifier_domains_are_dropped():
    """The voltage set carries no preamp, boost, bias or bandwidth surface."""
    capabilities = VoltageAmplifierCapabilities()
    dropped = (
        "preamp",
        "boost",
        "dc_bias",
        "bandwidth",
        "input_overcurrent",
        "input_compensation",
        "total_transconductance",
        "max_input_current_a",
    )

    for name in dropped:
        assert not hasattr(capabilities, name)

    assert [field.name for field in fields(VoltageAmplifierCapabilities)] == [
        "gain",
        "input_voltage",
        "output_voltage",
        "filters",
        "output_overload",
        "invert_output",
    ]
