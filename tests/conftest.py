"""Shared pytest configuration for the voltage amplifier tests.

No fixtures live here: the in-memory amplifiers in ``fakes`` are plain factory
functions with no shared state, and ``pyproject.toml`` already puts ``src`` on
``sys.path`` for the package imports.
"""
