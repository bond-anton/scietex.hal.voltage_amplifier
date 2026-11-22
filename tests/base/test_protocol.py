"""Tests for the ``VoltageAmplifierProtocol`` contract and the in-memory fakes."""

import inspect
import re

import pytest

try:
    from src.scietex.hal.voltage_amplifier.base import (
        AmplifierState,
        FilterSetting,
        FilterType,
        VoltageAmplifierCapabilities,
        VoltageAmplifierProtocol,
    )
except ModuleNotFoundError:
    from scietex.hal.voltage_amplifier.base import (
        AmplifierState,
        FilterSetting,
        FilterType,
        VoltageAmplifierCapabilities,
        VoltageAmplifierProtocol,
    )

from fakes import (  # noqa: E402
    full_amplifier,
    minimal_amplifier,
    read_only_gain_amplifier,
)

_CORE_METHODS = {"connect", "close", "read_data"}


class MinimalAmplifier(VoltageAmplifierProtocol):
    """Implements only the mandatory methods; optional ones keep the defaults."""

    capabilities = VoltageAmplifierCapabilities()

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def read_data(self) -> AmplifierState:
        return AmplifierState()


def _protocol_methods() -> set[str]:
    """Return the protocol's own public coroutines, excluding the mandatory three."""
    return {
        name
        for name, value in vars(VoltageAmplifierProtocol).items()
        if not name.startswith("_")
        and inspect.iscoroutinefunction(value)
        and name not in _CORE_METHODS
    }


def _protocol_properties() -> set[str]:
    """Return the protocol's own public properties."""
    return {
        name
        for name, value in vars(VoltageAmplifierProtocol).items()
        if isinstance(value, property)
    }


def _duck(*, omit: str | None = None) -> object:
    """Build a plain object exposing every protocol member except ``omit``.

    ``runtime_checkable`` checks attribute presence for all protocol members,
    including the ones with default implementations, so removing a single member
    is only observable on an otherwise complete object.
    """
    names = {
        name for name in dir(VoltageAmplifierProtocol) if not name.startswith("_")
    } - {"capabilities"}
    members = {name: (lambda *args, **kwargs: None) for name in names}
    if omit is not None:
        members.pop(omit)
    members["capabilities"] = VoltageAmplifierCapabilities()
    return type("DuckAmplifier", (), members)()


def test_minimal_subclass_satisfies_protocol():
    """A class inheriting the protocol and implementing the core methods passes."""
    assert isinstance(MinimalAmplifier(), VoltageAmplifierProtocol)


def test_duck_with_every_member_satisfies_protocol():
    """A non-subclass passes once every protocol member is present."""
    assert isinstance(_duck(), VoltageAmplifierProtocol)


def test_missing_read_data_fails_protocol_check():
    """The runtime check rejects an object that does not expose ``read_data``."""
    assert not isinstance(_duck(omit="read_data"), VoltageAmplifierProtocol)


def test_core_methods_alone_do_not_satisfy_protocol():
    """A duck with only connect/close/read_data fails.

    ``runtime_checkable`` also demands the defaulted optional methods and the
    ``supports_*``/``can_*`` properties, so the successful minimal
    implementation must inherit from the protocol.
    """

    class CoreOnly:
        capabilities = VoltageAmplifierCapabilities()

        async def connect(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def read_data(self) -> AmplifierState:
            return AmplifierState()

    assert not isinstance(CoreOnly(), VoltageAmplifierProtocol)


_OPTIONAL_CALLS = [
    ("get_gain", ()),
    ("set_gain", (10.0,)),
    ("get_filter", (FilterType.LOW_PASS,)),
    ("set_filter", (FilterSetting(type=FilterType.LOW_PASS),)),
    ("get_inverted", ()),
    ("set_inverted", (True,)),
]

_SUPPORT_PROPERTIES = [
    "supports_gain",
    "supports_filters",
    "supports_output_overload",
    "supports_invert_output",
]

_CAN_PROPERTIES = [
    "can_set_gain",
    "can_set_filter",
    "can_set_invert_output",
]


def test_optional_call_table_covers_every_optional_method():
    """The table matches the protocol exactly: six optional coroutines."""
    table_names = [name for name, _ in _OPTIONAL_CALLS]

    assert len(table_names) == len(set(table_names)) == 6
    assert set(table_names) == _protocol_methods()
    assert all(
        inspect.iscoroutinefunction(getattr(MinimalAmplifier, name))
        for name in table_names
    )


def test_property_tables_cover_every_property():
    """Four ``supports_*`` and three ``can_*`` properties, no more, no less."""
    assert len(_SUPPORT_PROPERTIES) == 4
    assert len(_CAN_PROPERTIES) == 3
    assert set(_SUPPORT_PROPERTIES + _CAN_PROPERTIES) == _protocol_properties()


@pytest.mark.asyncio
@pytest.mark.parametrize(("method_name", "args"), _OPTIONAL_CALLS)
async def test_optional_method_default_raises_not_implemented(method_name, args):
    """An unimplemented optional method reports its own name."""
    amplifier = MinimalAmplifier()

    with pytest.raises(NotImplementedError, match=re.escape(method_name)):
        await getattr(amplifier, method_name)(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "capability"),
    [
        ("get_gain", "gain"),
        ("set_gain", "gain"),
        ("get_filter", "filters"),
        ("set_filter", "filters"),
        ("get_inverted", "invert_output"),
        ("set_inverted", "invert_output"),
    ],
)
async def test_optional_messages_name_their_capability(method_name, capability):
    """The message points at the capability group the caller should consult."""
    amplifier = MinimalAmplifier()
    args = dict(_OPTIONAL_CALLS)[method_name]

    with pytest.raises(NotImplementedError) as excinfo:
        await getattr(amplifier, method_name)(*args)

    assert f"capability: {capability}" in str(excinfo.value)


@pytest.mark.parametrize("property_name", _SUPPORT_PROPERTIES + _CAN_PROPERTIES)
def test_default_capabilities_report_no_control(property_name):
    """With no advertised capability every convenience property is false."""
    assert getattr(minimal_amplifier(), property_name) is False


@pytest.mark.parametrize("property_name", _SUPPORT_PROPERTIES + _CAN_PROPERTIES)
def test_full_capabilities_expose_every_control(property_name):
    """A fully capable amplifier reports support and write access."""
    assert getattr(full_amplifier(), property_name) is True


def test_voltage_ranges_are_read_from_the_capability_set():
    """The ranges are static data, not controls, so they have no protocol method."""
    amplifier = full_amplifier()

    assert amplifier.capabilities.input_voltage.min_v == -10.0
    assert amplifier.capabilities.input_voltage.max_v == 10.0
    assert amplifier.capabilities.output_voltage.min_v == -10.0
    assert amplifier.capabilities.output_voltage.max_v == 10.0
    assert not hasattr(VoltageAmplifierProtocol, "supports_input_voltage")
    assert not hasattr(VoltageAmplifierProtocol, "supports_output_voltage")


@pytest.mark.asyncio
async def test_read_only_gain_is_readable_but_not_settable():
    """A read-only feature is advertised and readable without write access."""
    amplifier = read_only_gain_amplifier()

    assert amplifier.supports_gain is True
    assert amplifier.can_set_gain is False
    assert await amplifier.get_gain() == 10.0


@pytest.mark.asyncio
async def test_fake_absent_feature_raises_not_implemented():
    """The fake mirrors a driver by rejecting a feature it does not advertise."""
    with pytest.raises(NotImplementedError, match="get_gain"):
        await minimal_amplifier().get_gain()


@pytest.mark.asyncio
async def test_fake_read_data_requires_connection():
    """The snapshot is only available on an open transport."""
    amplifier = minimal_amplifier()

    with pytest.raises(RuntimeError, match="not connected"):
        await amplifier.read_data()

    await amplifier.connect()
    assert await amplifier.read_data() == AmplifierState()


@pytest.mark.asyncio
async def test_fake_reports_none_for_values_it_cannot_report():
    """Fields outside the advertised capability set stay ``None``."""
    amplifier = read_only_gain_amplifier()
    await amplifier.connect()

    state = await amplifier.read_data()
    assert state.gain == 10.0
    assert state.input_voltage_v is None
    assert state.output_overload is None
    assert state.filters == ()


@pytest.mark.asyncio
async def test_fake_setter_updates_the_snapshot():
    """A writable feature changes what the following read reports."""
    amplifier = full_amplifier()
    await amplifier.connect()

    await amplifier.set_gain(1.0)
    await amplifier.set_inverted(True)

    state = await amplifier.read_data()
    assert state.gain == 1.0
    assert state.inverted is True


@pytest.mark.asyncio
async def test_full_amplifier_gain_is_enumerated_and_settable():
    """The gain offers at least three values and accepts a new one."""
    amplifier = full_amplifier()

    assert len(amplifier.capabilities.gain.gain.options) >= 3
    assert amplifier.can_set_gain is True

    await amplifier.set_gain(100.0)
    assert await amplifier.get_gain() == 100.0


@pytest.mark.asyncio
async def test_fake_gain_requires_write_access():
    """A readable gain refuses writes it does not advertise."""
    amplifier = read_only_gain_amplifier()

    with pytest.raises(NotImplementedError, match="no write capability"):
        await amplifier.set_gain(100.0)
