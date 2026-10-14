"""The schema catalogue itself: well-formed, complete and bound to the domain.

Every ``ter.<name>/<version>`` id the source names either ships a JSON Schema
or is listed in :data:`PENDING`, the formats still waiting for one. The list
only shrinks: a format that gains a schema must leave it, and a new format
cannot be written without either a schema or a deliberate entry here.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ter.adapters.driven.json_schemas import (
    UnknownSchemaError,
    published_schemas,
    schema,
    schema_path,
)
from ter.domain.events import EVENT_SCHEMA_VERSION, READABLE_SCHEMA_VERSIONS

SRC = Path(__file__).resolve().parents[2] / "src" / "ter"
SCHEMA_ID = re.compile(r"[\"'](ter\.[a-z0-9_-]+/[0-9][0-9.]*)[\"']")

#: Formats TER writes or reads that have no JSON Schema yet. Remove an entry
#: in the change that ships its schema; never add one without a reason.
PENDING: dict[str, str] = {
    "ter.a3/0.1": "A3 view-model (a3 --json)",
    "ter.context-bundle/1": "context bundle",
    "ter.context-measures/1": "context measures",
    "ter.control-report/1": "control chart report",
    "ter.corpus-by-stack/1": "corpus comparison by stack",
    "ter.corpus-manifest/1": "imported corpus manifest",
    "ter.critical-evidence/1": "critical evidence summary",
    "ter.evidence-graph/1": "evidence graph (inside ter.lean)",
    "ter.evidence/0.1": "evidence edge (inside ter.lean)",
    "ter.hook-check/1": "hook check report",
    "ter.hook-recording/1": "hook recording",
    "ter.lean/0.1": "L2 explanation (explain --json)",
    "ter.policy-effectiveness/1": "policy effectiveness report",
    "ter.price_book/1": "price book data file",
    "ter.redaction-report/1": "corpus redaction report",
    "ter.req-trace/1": "requirements trace (pytest --req-trace)",
    "ter.route/0.1": "routing decision",
    "ter.routing_profile/1": "routing profile data file",
    "ter.session-control/1": "a session placed against control limits",
}

#: Event contract versions this build still reads: each is a subset of the
#: current one, whose schema covers them (TER-SRC-012).
LEGACY = READABLE_SCHEMA_VERSIONS - {EVENT_SCHEMA_VERSION}


def _named_in_source() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        found.update(SCHEMA_ID.findall(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.req("TER-TST-001")
def test_every_format_the_source_names_has_a_schema_or_is_pending() -> None:
    named = _named_in_source() - LEGACY
    missing = sorted(named - set(published_schemas()) - set(PENDING))
    assert not missing, (
        "these formats have no JSON Schema under src/ter/data/schemas and are "
        f"not in PENDING: {missing}"
    )


@pytest.mark.req("TER-TST-001")
def test_pending_only_shrinks() -> None:
    shipped = sorted(set(PENDING) & set(published_schemas()))
    assert not shipped, f"remove from PENDING, their schemas ship: {shipped}"
    gone = sorted(set(PENDING) - _named_in_source())
    assert not gone, f"remove from PENDING, the source no longer names: {gone}"


@pytest.mark.req("TER-TST-001")
def test_the_current_event_contract_ships_a_schema() -> None:
    assert EVENT_SCHEMA_VERSION in published_schemas()


@pytest.mark.req("TER-TST-001")
@pytest.mark.parametrize("schema_id", published_schemas())
def test_schema_is_a_valid_draft_07_schema_named_by_its_id(schema_id: str) -> None:
    doc = schema(schema_id)
    jsonschema.Draft7Validator.check_schema(doc)
    assert doc["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert doc["$id"] == f"urn:ter:schema:{schema_id}"
    assert doc["title"] == schema_id
    assert doc.get("description"), "say what writes and reads this format"


@pytest.mark.req("TER-TST-001")
@pytest.mark.parametrize("schema_id", published_schemas())
def test_every_document_shape_pins_its_schema_field(schema_id: str) -> None:
    doc = schema(schema_id)
    shapes = (
        [
            doc["definitions"][branch["$ref"].removeprefix("#/definitions/")]
            for branch in doc["oneOf"]
        ]
        if "oneOf" in doc
        else [doc]
    )
    for shape in shapes:
        assert shape["properties"]["schema"] == {"const": schema_id}
        assert "schema" in shape["required"]


@pytest.mark.parametrize("schema_id", published_schemas())
def test_schema_file_is_canonically_formatted(schema_id: str) -> None:
    text = schema_path(schema_id).read_text(encoding="utf-8")
    canonical = json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    assert text == canonical, "format with json.dumps(indent=2) plus a newline"


def test_an_unknown_schema_id_is_refused() -> None:
    with pytest.raises(UnknownSchemaError):
        schema("ter.no-such-format/1")
    with pytest.raises(UnknownSchemaError):
        schema("not-a-ter-id")


# --- enumerations bound to the domain --------------------------------------


def _bindings(node: Any, where: str = "") -> Iterator[tuple[str, str, list[Any]]]:
    if isinstance(node, dict):
        if "x-ter-enum" in node:
            yield where, node["x-ter-enum"], node["enum"]
        for key, value in node.items():
            yield from _bindings(value, f"{where}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _bindings(value, f"{where}/{i}")


def _domain_values(binding: str) -> list[str]:
    module_name, _, rest = binding.partition(":")
    name, _, attribute = rest.partition("#")
    value = getattr(importlib.import_module(module_name), name)
    if isinstance(value, type) and issubclass(value, Enum):
        return [member.value for member in value]
    return [getattr(item, attribute) if attribute else item for item in value]


_BOUND = [
    pytest.param(schema_id, where, binding, values, id=f"{schema_id}{where}")
    for schema_id in published_schemas()
    for where, binding, values in _bindings(schema(schema_id))
]


@pytest.mark.req("TER-TST-004")
def test_some_enumerations_are_bound() -> None:
    assert len(_BOUND) >= 10


@pytest.mark.req("TER-TST-004")
@pytest.mark.parametrize(("schema_id", "where", "binding", "values"), _BOUND)
def test_schema_enumeration_equals_the_domain_enumeration(
    schema_id: str, where: str, binding: str, values: list[Any]
) -> None:
    expected = _domain_values(binding)
    listed = [v for v in values if v is not None]
    assert listed == expected, (
        f"{schema_id} {where} copies {binding}; update the schema (and its "
        "version, when readers would notice) to match the domain"
    )
