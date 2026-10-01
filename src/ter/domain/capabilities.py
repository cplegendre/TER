"""Capabilities: adapters that plug into a port, named ``<Port>.<adapter>``.

A capability is one adapter for one driven port, registered under the
``ter.capabilities`` entry-point group (ADR 0005). Its key names the port and
the adapter, for example ``OutcomeSource.junit``. These are the pure value
types; discovery and loading live in ``ter.bootstrap.capabilities``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CAPABILITY_GROUP",
    "Capability",
    "CapabilityError",
    "CapabilityProblem",
    "UnknownCapabilityError",
    "capability_key",
    "parse_capability_key",
]

#: The entry-point group every capability is registered under (ADR 0005).
CAPABILITY_GROUP = "ter.capabilities"

_PORT = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_ADAPTER = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CapabilityError(Exception):
    """A capability could not be resolved, loaded or does not fit its port."""


class UnknownCapabilityError(CapabilityError, LookupError):
    """No capability of that name is registered for the port."""


@dataclass(frozen=True, slots=True)
class Capability:
    """One registered adapter for one port.

    ``target`` is the ``module:attribute`` the capability loads from, and
    ``origin`` says who registered it: ``built-in`` for TER's own adapters, or
    the distribution that declared the entry point.
    """

    port: str
    name: str
    target: str
    origin: str

    @property
    def key(self) -> str:
        return capability_key(self.port, self.name)


@dataclass(frozen=True, slots=True)
class CapabilityProblem:
    """A capability that was registered but cannot be used, and why."""

    key: str
    target: str
    reason: str


def capability_key(port: str, name: str) -> str:
    return f"{port}.{name}"


def parse_capability_key(key: str) -> tuple[str, str]:
    """Split ``<Port>.<adapter>`` into its port and adapter names.

    The port is a Protocol class name (``OutcomeSource``); the adapter name is
    lower case letters, digits, ``-`` and ``_`` (``junit``, ``claude-code``).
    """
    port, dot, name = key.partition(".")
    if not dot or not _PORT.match(port) or not _ADAPTER.match(name):
        raise CapabilityError(
            f"capability name {key!r} is not <Port>.<adapter> "
            "(for example 'OutcomeSource.junit')"
        )
    return port, name
