"""
Voltage amplifier driver registry.

Drivers are advertised through the ``scietex.hal.voltage_amplifier.drivers``
entry point group declared in this package's ``pyproject.toml``. Consumers
resolve a driver by its entry point name rather than by a dotted class path, so
the stored identifier stays stable when modules move.

Entry points are read from installed distribution metadata. A driver added to
the source tree only appears after the package is reinstalled.
"""

from importlib.metadata import entry_points
from typing import cast

from .base.protocol import VoltageAmplifierProtocol

ENTRY_POINT_GROUP = "scietex.hal.voltage_amplifier.drivers"


def discover_drivers() -> dict[str, type[VoltageAmplifierProtocol]]:
    """
    Map every advertised driver's entry point name to its class.

    Returns:
        dict[str, type[VoltageAmplifierProtocol]]: Entry point name to driver
            class, sorted by name.

    Raises:
        TypeError: If an entry point resolves to something that is not a class.
    """
    discovered: dict[str, type[VoltageAmplifierProtocol]] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        driver = entry_point.load()
        if not isinstance(driver, type):
            raise TypeError(
                f"Entry point {entry_point.name!r} resolved to "
                f"{type(driver).__name__}, not a class"
            )
        discovered[entry_point.name] = cast(type[VoltageAmplifierProtocol], driver)
    return dict(sorted(discovered.items()))


def resolve_driver(name: str) -> type[VoltageAmplifierProtocol]:
    """
    Resolve a single driver by entry point name.

    Args:
        name: Entry point name, e.g. ``scietex_modbus_v1``.

    Returns:
        type[VoltageAmplifierProtocol]: The driver class.

    Raises:
        ValueError: If no advertised driver carries that name. The message
            lists the names that are available.
    """
    drivers = discover_drivers()
    try:
        return drivers[name]
    except KeyError:
        available = ", ".join(drivers) or "none"
        raise ValueError(
            f"Unknown voltage amplifier driver {name!r}: not advertised under "
            f"{ENTRY_POINT_GROUP!r}; available: {available}"
        ) from None
