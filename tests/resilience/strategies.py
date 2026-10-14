"""Hypothesis strategies for hostile input: any JSON, and real documents
with a few of their values swapped for other JSON, removed or retyped.

Mutating real documents reaches far deeper into a reader than random JSON
does: most of the document still parses, so the reader gets as far as the
one field that is wrong, which is where shape assumptions break.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterator
from typing import Any

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st


def fuzz(examples: int) -> settings:
    """Settings for a fuzzer: ``examples`` per run, times ``--fuzz-scale``."""
    scale = max(1, int(os.environ.get("TER_FUZZ_SCALE", "1")))
    return settings(
        max_examples=examples * scale,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )


_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**63),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=12),
    # Strings readers look for, in the wrong place.
    st.sampled_from(
        ["", "user", "assistant", "tool_use", "text", "Stop", "1970-01-01T00:00:00Z"]
    ),
)

#: Any JSON value, kept small so examples stay fast.
json_values = st.recursive(
    _scalars,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.dictionaries(st.text(max_size=8), inner, max_size=3),
    ),
    max_leaves=6,
)

Path = tuple[str | int, ...]


def _paths(doc: Any, here: Path = ()) -> Iterator[Path]:
    """Every node of ``doc`` below the root."""
    if isinstance(doc, dict):
        for key, value in doc.items():
            yield (*here, key)
            yield from _paths(value, (*here, key))
    elif isinstance(doc, list):
        for i, value in enumerate(doc):
            yield (*here, i)
            yield from _paths(value, (*here, i))


def _parent(doc: Any, path: Path) -> Any:
    for step in path[:-1]:
        doc = doc[step]
    return doc


@st.composite
def mutated(draw: st.DrawFn, doc: Any, max_mutations: int = 3) -> Any:
    """``doc`` with one to ``max_mutations`` nodes replaced, removed or
    emptied."""
    doc = copy.deepcopy(doc)
    for _ in range(draw(st.integers(1, max_mutations))):
        paths = list(_paths(doc))
        if not paths:
            return draw(json_values)
        path = draw(st.sampled_from(paths))
        parent = _parent(doc, path)
        action = draw(st.sampled_from(["replace", "remove", "empty", "stringify"]))
        if action == "remove":
            del parent[path[-1]]
        elif action == "empty":
            old = parent[path[-1]]
            parent[path[-1]] = (
                type(old)() if isinstance(old, (dict, list, str)) else None
            )
        elif action == "stringify":
            parent[path[-1]] = json.dumps(parent[path[-1]])
        else:
            parent[path[-1]] = draw(json_values)
    return doc


def jsonl_lines(records: list[Any]) -> st.SearchStrategy[list[str]]:
    """Lines of a JSONL file built from ``records``: kept, mutated, dropped,
    repeated or reordered, with the odd torn or non-JSON line."""
    line = st.one_of(
        st.sampled_from(records).map(json.dumps),
        st.sampled_from(records).flatmap(mutated).map(json.dumps),
        json_values.map(json.dumps),
        st.sampled_from(records).map(
            lambda r: json.dumps(r)[: len(json.dumps(r)) // 2]
        ),
        st.sampled_from(["", "not json", "[", "﻿{}"]),
    )
    return st.lists(line, min_size=1, max_size=len(records) + 4)
