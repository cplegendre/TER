"""Helpers for the schema contract suite (TER-TST-001 to TER-TST-005).

``validator(schema_id)`` checks a document against the JSON Schema TER ships
for it. Formats follow TER's readers: ``date-time`` and ``date`` are what
Python's ``datetime.fromisoformat`` and ``date.fromisoformat`` read, which is
what ``isoformat()`` writes. ``documents(schema_id)`` is a hypothesis strategy
for documents the schema allows, generating those formats the same way.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import warnings
from functools import cache
from typing import Any

import jsonschema
from hypothesis.errors import HypothesisWarning
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from ter.adapters.driven.json_schemas import schema

FORMATS = jsonschema.FormatChecker(formats=())


@FORMATS.checks("date-time", raises=ValueError)
def _datetime(value: object) -> bool:
    return not isinstance(value, str) or bool(datetime.fromisoformat(value))


@FORMATS.checks("date", raises=ValueError)
def _date(value: object) -> bool:
    return not isinstance(value, str) or bool(date.fromisoformat(value))


@cache
def validator(schema_id: str) -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(schema(schema_id), format_checker=FORMATS)


def errors(schema_id: str, document: object) -> list[str]:
    """Every reason ``document`` fails its schema, empty when it conforms."""
    found = validator(schema_id).iter_errors(document)
    return sorted(f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in found)


def assert_conforms(schema_id: str, document: object) -> None:
    problems = errors(schema_id, document)
    assert not problems, f"not a valid {schema_id} document:\n" + "\n".join(problems)


_offsets = st.sampled_from(
    [None, UTC, timezone(timedelta(hours=1)), timezone(timedelta(hours=-5))]
)
_moments = st.builds(
    lambda moment, tz: moment.replace(tzinfo=tz),
    st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2100, 1, 1)),
    _offsets,
)
CUSTOM_FORMATS: dict[str, st.SearchStrategy[str]] = {
    "date-time": _moments.map(lambda m: m.isoformat()),
    "date": st.dates(min_value=date(1990, 1, 1)).map(lambda d: d.isoformat()),
}


def inline_refs(node: Any, definitions: dict[str, Any]) -> Any:
    """``node`` with every local ``#/definitions/...`` reference inlined.

    hypothesis-jsonschema loses the base of a reference nested inside a
    referenced definition; TER's schemas have no recursive definitions, so
    inlining them is exact.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            target = inline_refs(
                definitions[ref.removeprefix("#/definitions/")], definitions
            )
            rest = {
                k: inline_refs(v, definitions) for k, v in node.items() if k != "$ref"
            }
            return {**target, **rest} if rest else target
        return {
            k: inline_refs(v, definitions)
            for k, v in node.items()
            if k not in ("definitions", "$id", "$schema")
        }
    if isinstance(node, list):
        return [inline_refs(v, definitions) for v in node]
    return node


def documents(schema_id: str) -> st.SearchStrategy[Any]:
    """Documents the schema for ``schema_id`` allows."""
    loaded = schema(schema_id)
    flat = inline_refs(loaded, loaded.get("definitions", {}))
    with warnings.catch_warnings():
        # Overriding date-time and date is the point: TER's readers define them.
        warnings.simplefilter("ignore", HypothesisWarning)
        return from_schema(flat, custom_formats=CUSTOM_FORMATS)
