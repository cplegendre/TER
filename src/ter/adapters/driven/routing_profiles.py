"""Routing profile adapters behind the :class:`~ter.ports.driven.RoutingProfiles` port.

:class:`JsonRoutingProfiles` reads ``ter.routing_profile/1`` JSON files, one
profile per file. With no directory it reads the profiles shipped inside the
``ter`` package (``ter/data/routing_profiles/*.json``), so which model a role
means changes by editing data, never code (TER-RTE-001).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ...domain.routing import (
    ModelBinding,
    RoutingProfile,
    RoutingProfileError,
    TaskKind,
)

__all__ = [
    "DEFAULT_PROFILE",
    "ROUTING_PROFILE_SCHEMA",
    "JsonRoutingProfiles",
    "default_routing_profiles",
    "parse_routing_profile",
]

ROUTING_PROFILE_SCHEMA = "ter.routing_profile/1"
#: The profile used when none is named, when a set of profiles holds it.
DEFAULT_PROFILE = "default"


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RoutingProfileError(f"{where} must be a list of non-empty strings")
    return list(value)


def _mapping(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and k and v for k, v in value.items()
    ):
        raise RoutingProfileError(f"{where} must map names to names")
    return dict(value)


def parse_routing_profile(document: Any, where: str = "profile") -> RoutingProfile:
    """Validate a decoded routing profile document and return the profile.

    Raises:
        RoutingProfileError: If the document is not a ``ter.routing_profile/1``
            profile, or names a role, task kind or field it does not define.
    """
    if not isinstance(document, dict):
        raise RoutingProfileError(f"{where}: a routing profile must be a JSON object")
    if document.get("schema") != ROUTING_PROFILE_SCHEMA:
        raise RoutingProfileError(
            f"{where}: unsupported routing profile schema "
            f"{document.get('schema')!r}; expected {ROUTING_PROFILE_SCHEMA!r}"
        )
    name = document.get("name")
    if not isinstance(name, str) or not name:
        raise RoutingProfileError(f"{where}: 'name' must be a non-empty string")
    raw_roles = document.get("roles")
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise RoutingProfileError(f"{where}: 'roles' must be a non-empty object")
    roles: dict[str, ModelBinding] = {}
    for role, raw in raw_roles.items():
        if not isinstance(raw, dict):
            raise RoutingProfileError(f"{where}: roles.{role} must be an object")
        provider, model = raw.get("provider"), raw.get("model")
        if not isinstance(provider, str) or not provider:
            raise RoutingProfileError(f"{where}: roles.{role}.provider is missing")
        if not isinstance(model, str) or not model:
            raise RoutingProfileError(f"{where}: roles.{role}.model is missing")
        roles[role] = ModelBinding(provider, model)
    task_roles: dict[TaskKind, str] = {}
    for kind, role in _mapping(
        document.get("task_roles"), f"{where}: task_roles"
    ).items():
        try:
            task_roles[TaskKind(kind)] = role
        except ValueError:
            raise RoutingProfileError(
                f"{where}: unknown task kind {kind!r} "
                f"(known: {', '.join(k.value for k in TaskKind)})"
            ) from None
    source = document.get("source", "")
    description = document.get("description", "")
    if not isinstance(source, str) or not source:
        raise RoutingProfileError(
            f"{where}: 'source' must say where the profile came from"
        )
    if not isinstance(description, str):
        raise RoutingProfileError(f"{where}: 'description' must be a string")
    return RoutingProfile(
        name=name,
        roles=roles,
        task_roles=task_roles,
        escalation=_mapping(document.get("escalation", {}), f"{where}: escalation"),
        escalate_on=frozenset(
            _strings(document.get("escalate_on", []), f"{where}: escalate_on")
        ),
        description=description,
        source=source,
    )


class JsonRoutingProfiles:
    """Routing profiles loaded from a directory of ``*.json`` files."""

    def __init__(self, directory: str | Path | None = None) -> None:
        files: list[Traversable]
        if directory is None:
            root = resources.files("ter").joinpath("data", "routing_profiles")
            self.name = "ter:data/routing_profiles"
        else:
            root = Path(directory)
            self.name = str(directory)
        try:
            files = sorted(
                (f for f in root.iterdir() if f.name.endswith(".json")),
                key=lambda f: f.name,
            )
        except OSError as exc:
            raise RoutingProfileError(f"{self.name}: cannot read: {exc}") from exc
        self._profiles: dict[str, RoutingProfile] = {}
        for f in files:
            try:
                document = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RoutingProfileError(f"{f.name}: not JSON: {exc}") from exc
            except (OSError, UnicodeDecodeError) as exc:
                raise RoutingProfileError(f"{f.name}: cannot read: {exc}") from exc
            profile = parse_routing_profile(document, f.name)
            if profile.name in self._profiles:
                raise RoutingProfileError(
                    f"{f.name}: profile {profile.name!r} is defined twice"
                )
            self._profiles[profile.name] = profile
        if not self._profiles:
            raise RoutingProfileError(f"{self.name}: no routing profiles")

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def default(self) -> str:
        return DEFAULT_PROFILE if DEFAULT_PROFILE in self._profiles else self.names()[0]

    def profile(self, name: str) -> RoutingProfile:
        try:
            return self._profiles[name]
        except KeyError:
            raise RoutingProfileError(
                f"Unknown routing profile {name!r} (known: {', '.join(self.names())})"
            ) from None


@lru_cache(maxsize=1)
def default_routing_profiles() -> JsonRoutingProfiles:
    """The routing profiles shipped with TER, loaded once per process."""
    return JsonRoutingProfiles()
