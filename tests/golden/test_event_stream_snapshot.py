"""Golden snapshot of the normalised ``ter.event`` stream for the corpus.

Freezes event identities, order, kinds and provenance so that any change to
reconstruction (TER-SRC-004) or to the contract itself is a visible diff.
"""

from __future__ import annotations

from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.domain import SessionTrace

from .conftest import CORPUS, assert_matches_snapshot


def project(trace: SessionTrace) -> dict[str, Any]:
    return {
        "schema": trace.schema_version,
        "session_id": trace.session_id,
        "source_format": trace.source_format,
        "coverage": round(trace.coverage, 6),
        "unrecognised": dict(sorted(trace.unrecognised_by_type.items())),
        "metadata": dict(sorted(trace.metadata_by_type.items())),
        "events": [
            [
                e.sequence,
                e.id,
                e.kind.value,
                e.actor.value,
                e.tool.kind.value if e.tool else None,
                e.tool.native_name if e.tool else None,
                list(e.provenance.lines),
                e.usage.output_tokens if e.usage else None,
            ]
            for e in trace.events
        ],
    }


@pytest.mark.req("TER-SRC-004")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_event_stream_matches_golden_snapshot(name: str) -> None:
    trace = ClaudeCodeJsonlSource().read(CORPUS[name])
    assert_matches_snapshot(f"{name}.events", project(trace))
