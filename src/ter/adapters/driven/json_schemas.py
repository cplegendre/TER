"""JSON Schemas for the versioned documents TER writes and reads (TER-TST-001).

Every ``ter.*`` document carries its schema id in a ``schema`` field, such
as ``ter.intervention-policies/1``. The JSON Schema (draft-07) for that id
ships inside the package at ``ter/data/schemas/<name>/<version>.json``, so a
tool in any language can check a file before handing it to TER, and TER's
tests hold every writer and reader to it (``tests/schema``).

Enumerations in a schema name the domain enumeration they copy with an
``x-ter-enum`` annotation (``module:Name``, or ``module:NAME#attribute`` for a
collection of objects); a test fails when the two drift apart.

Reading a schema needs only the standard library. Validating against one
needs a JSON Schema validator (the tests use ``jsonschema``), which TER
itself does not depend on.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

__all__ = ["UnknownSchemaError", "published_schemas", "schema", "schema_path"]

_ROOT = ("data", "schemas")


class UnknownSchemaError(KeyError):
    """No JSON Schema ships for this schema id."""


def _root() -> Traversable:
    return resources.files("ter").joinpath(*_ROOT)


def _split(schema_id: str) -> tuple[str, str]:
    name, sep, version = schema_id.partition("/")
    if not sep or not name.startswith("ter.") or not version or "/" in version:
        raise UnknownSchemaError(f"not a TER schema id: {schema_id!r}")
    return name, version


def schema_path(schema_id: str) -> Traversable:
    """Where the JSON Schema for ``schema_id`` ships inside the package."""
    name, version = _split(schema_id)
    return _root().joinpath(name, f"{version}.json")


def published_schemas() -> tuple[str, ...]:
    """Every schema id with a shipped JSON Schema, sorted."""
    ids = [
        f"{folder.name}/{file.name.removesuffix('.json')}"
        for folder in _root().iterdir()
        if folder.is_dir() and folder.name.startswith("ter.")
        for file in folder.iterdir()
        if file.name.endswith(".json")
    ]
    return tuple(sorted(ids))


@cache
def _load(schema_id: str) -> str:
    path = schema_path(schema_id)
    if not path.is_file():
        raise UnknownSchemaError(f"no JSON Schema ships for {schema_id!r}")
    return path.read_text(encoding="utf-8")


def schema(schema_id: str) -> dict[str, Any]:
    """The JSON Schema for ``schema_id``, as a fresh dict.

    Raises:
        UnknownSchemaError: When ``schema_id`` is not a ``ter.<name>/<version>``
            id, or no schema ships for it.
    """
    loaded: dict[str, Any] = json.loads(_load(schema_id))
    return loaded
