"""Scietex voltage amplifier HAL package.

A ``VoltageAmplifier`` scales an input voltage: the driver layer exposes its
capability set, its state snapshot, and the transport-agnostic control contract
that concrete protocols implement.
"""

from .version import __version__

__all__ = ["__version__"]
