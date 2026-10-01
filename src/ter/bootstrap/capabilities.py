"""The capability registry: adapters for ports, discovered and loaded lazily.

TER's own adapters are built in (:data:`BUILTIN_CAPABILITIES`); an installed
package adds more by declaring entry points in the ``ter.capabilities`` group
(ADR 0005)::

    [project.entry-points."ter.capabilities"]
    "OutcomeSource.junit" = "ter.adapters.driven.junit:JUnitOutcomeSource"

The registry

* resolves a built-in without scanning installed packages, so a hook pays
  nothing for discovery;
* imports a capability's module only when a use case asks for it;
* rejects an object that does not satisfy its port's Protocol, naming the
  missing members;
* never raises out of discovery: a broken or conflicting entry is recorded
  in :attr:`CapabilityRegistry.problems` and the rest keep working;
* never lets an entry point replace a built-in of the same name.

Discovery reads installed package metadata, which is why the registry lives
in the composition root rather than the core.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

from ..domain.capabilities import (
    CAPABILITY_GROUP,
    Capability,
    CapabilityError,
    CapabilityProblem,
    UnknownCapabilityError,
    parse_capability_key,
)
from ..ports import DRIVEN_PORTS

__all__ = [
    "BUILTIN_CAPABILITIES",
    "BUILTIN_ORIGIN",
    "CapabilityRegistry",
    "EntryLike",
    "default_registry",
    "installed_entry_points",
]

#: TER's own adapters, by capability key. ``pyproject.toml`` declares the same
#: table as entry points; ``tests/unit/test_ter4_capabilities.py`` keeps the
#: two equal. Built-ins also work from a source checkout that is not installed.
BUILTIN_CAPABILITIES: dict[str, str] = {
    "Embedder.hashing": "ter.adapters.driven.embedders:HashingEmbedder",
    "EventLog.jsonl": "ter.adapters.driven.event_log:JsonlEventLog",
    "PriceBook.json": "ter.adapters.driven.pricing:JsonPriceBook",
    "SessionSource.claude-code": "ter.adapters.driven.claude_code:ClaudeCodeJsonlSource",
    "TerScorer.ter3": "ter.adapters.driven.ter3:Ter3Scorer",
    "Tokenizer.regex": "ter.adapters.driven.tokenizers:RegexTokenizer",
    "Tokenizer.tiktoken": "ter.adapters.driven.tokenizers:TiktokenTokenizer",
}

BUILTIN_ORIGIN = "built-in"


class EntryLike(Protocol):
    """What the registry needs of an entry point (``importlib.metadata.EntryPoint``)."""

    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...

    def load(self) -> Any: ...


@dataclass(frozen=True)
class _Target:
    """A built-in ``module:attribute`` reference, loaded like an entry point."""

    name: str
    value: str

    def load(self) -> Any:
        module, _, attribute = self.value.partition(":")
        loaded: Any = importlib.import_module(module)
        for part in attribute.split(".") if attribute else ():
            loaded = getattr(loaded, part)
        return loaded


def installed_entry_points() -> tuple[EntryLike, ...]:
    """Every installed entry point in the ``ter.capabilities`` group."""
    from importlib.metadata import entry_points

    return tuple(entry_points(group=CAPABILITY_GROUP))


def _origin(entry: EntryLike) -> str:
    dist = getattr(entry, "dist", None)
    name = getattr(dist, "name", None)
    return f"entry point ({name})" if name else "entry point"


def _methods(port: type[object]) -> tuple[str, ...]:
    """The callable members a port Protocol declares."""
    return tuple(
        sorted(
            n for n, v in vars(port).items() if not n.startswith("_") and callable(v)
        )
    )


def _members(port: type[object]) -> tuple[str, ...]:
    annotations: Mapping[str, object] = vars(port).get("__annotations__", {})
    return tuple(
        sorted(set(_methods(port)) | {n for n in annotations if not n.startswith("_")})
    )


@dataclass
class _Slot:
    capability: Capability
    entry: EntryLike
    factory: Callable[..., object] | None = None


class CapabilityRegistry:
    """Capabilities by port and adapter name; see the module docstring."""

    def __init__(
        self,
        *,
        builtins: Mapping[str, str] = BUILTIN_CAPABILITIES,
        discover: Callable[[], Iterable[EntryLike]] | None = installed_entry_points,
        ports: Mapping[str, type[object]] = DRIVEN_PORTS,
    ) -> None:
        self._ports = dict(ports)
        self._discover = discover
        self._discovered = discover is None
        self._slots: dict[tuple[str, str], _Slot] = {}
        self._problems: list[CapabilityProblem] = []
        for key, target in builtins.items():
            self._admit(_Target(key, target), BUILTIN_ORIGIN)

    # -- registration ------------------------------------------------------

    def _problem(self, key: str, target: str, reason: str) -> None:
        problem = CapabilityProblem(key, target, reason)
        if problem not in self._problems:
            self._problems.append(problem)

    def _admit(self, entry: EntryLike, origin: str) -> None:
        try:
            key, target = str(entry.name), str(entry.value)
        except Exception as exc:  # a malformed entry object must not stop discovery
            self._problem("?", "?", f"unreadable entry point: {exc}")
            return
        try:
            port, name = parse_capability_key(key)
        except CapabilityError as exc:
            self._problem(key, target, str(exc))
            return
        if port not in self._ports:
            known = ", ".join(sorted(self._ports))
            self._problem(key, target, f"unknown port {port!r} (known: {known})")
            return
        existing = self._slots.get((port, name))
        if existing is not None:
            if existing.capability.target != target:
                self._problem(
                    key,
                    target,
                    f"{key} is already registered by {existing.capability.origin} "
                    f"as {existing.capability.target}; this entry is ignored",
                )
            return
        self._slots[(port, name)] = _Slot(Capability(port, name, target, origin), entry)

    def _ensure_discovered(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        assert self._discover is not None
        try:
            entries = tuple(self._discover())
        except Exception as exc:  # broken package metadata must not crash TER
            self._problem(CAPABILITY_GROUP, "-", f"entry point discovery failed: {exc}")
            return
        for entry in entries:
            self._admit(entry, _origin(entry))

    # -- queries -----------------------------------------------------------

    @property
    def problems(self) -> tuple[CapabilityProblem, ...]:
        """Registered capabilities that cannot be used, after discovery."""
        self._ensure_discovered()
        return tuple(self._problems)

    def capabilities(self, port: str | None = None) -> tuple[Capability, ...]:
        """Every usable-looking registration (not yet loaded), sorted by key."""
        self._ensure_discovered()
        return tuple(
            slot.capability
            for (p, _), slot in sorted(self._slots.items())
            if port is None or p == port
        )

    def names(self, port: str) -> tuple[str, ...]:
        return tuple(c.name for c in self.capabilities(port))

    def _slot(self, port: str, name: str) -> _Slot:
        if port not in self._ports:
            raise UnknownCapabilityError(f"unknown port {port!r}")
        slot = self._slots.get((port, name))
        if slot is None:
            self._ensure_discovered()
            slot = self._slots.get((port, name))
        if slot is None:
            available = ", ".join(self.names(port)) or "none"
            raise UnknownCapabilityError(
                f"no {port} capability named {name!r} (available: {available})"
            )
        return slot

    # -- loading -----------------------------------------------------------

    def factory(self, port: str, name: str) -> Callable[..., object]:
        """Load (once) the class or factory registered as ``<port>.<name>``.

        Raises :class:`CapabilityError` when it fails to import, is not
        callable, or is a class that lacks a method of the port.
        """
        slot = self._slot(port, name)
        if slot.factory is not None:
            return slot.factory
        cap = slot.capability
        try:
            loaded = slot.entry.load()
        except Exception as exc:
            reason = f"failed to load: {type(exc).__name__}: {exc}"
            self._problem(cap.key, cap.target, reason)
            raise CapabilityError(
                f"capability {cap.key} ({cap.target}) {reason}"
            ) from exc
        if not callable(loaded):
            reason = f"{type(loaded).__name__} object is not a class or factory"
            self._problem(cap.key, cap.target, reason)
            raise CapabilityError(f"capability {cap.key} ({cap.target}): {reason}")
        if isinstance(loaded, type):
            missing = [
                m
                for m in _methods(self._ports[port])
                if not callable(getattr(loaded, m, None))
            ]
            if missing:
                reason = (
                    f"does not satisfy the {port} port: missing {', '.join(missing)}"
                )
                self._problem(cap.key, cap.target, reason)
                raise CapabilityError(f"capability {cap.key} ({cap.target}) {reason}")
        factory: Callable[..., object] = loaded
        slot.factory = factory
        return factory

    def create(self, port: str, name: str, *args: object, **kwargs: object) -> object:
        """Build the capability and check the instance satisfies its port."""
        cap = self._slot(port, name).capability
        built = self.factory(port, name)(*args, **kwargs)
        protocol = self._ports[port]
        if not isinstance(built, protocol):
            missing = [m for m in _members(protocol) if not hasattr(built, m)]
            reason = f"does not satisfy the {port} port: missing {', '.join(missing) or 'members'}"
            self._problem(cap.key, cap.target, reason)
            raise CapabilityError(f"capability {cap.key} ({cap.target}) {reason}")
        return built

    def check(self) -> tuple[CapabilityProblem, ...]:
        """Load every registered capability and return every problem found.

        Never raises; nothing is instantiated, so no adapter does IO.
        """
        for cap in self.capabilities():
            try:
                self.factory(cap.port, cap.name)
            except CapabilityError:
                pass  # recorded as a problem
        return self.problems


@cache
def default_registry() -> CapabilityRegistry:
    """The installation's registry: built-ins plus installed entry points."""
    return CapabilityRegistry()
