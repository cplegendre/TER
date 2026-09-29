"""Unit tests for the TER 4 domain: maturity levels and the event model."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ter import EVENT_SCHEMA_VERSION
from ter.domain import (
    Actor,
    Event,
    EventKind,
    Maturity,
    Provenance,
    SessionTrace,
    TokenUsage,
    UnrecognisedRecord,
    make_event_id,
)


class TestMaturity:
    def test_levels_are_ordered_l0_to_l6(self) -> None:
        assert [m.code for m in Maturity] == [f"L{i}" for i in range(7)]
        assert Maturity.MEASURED < Maturity.GROUNDED < Maturity.LEARNING

    @pytest.mark.parametrize(
        "value", ["L3", "l3", "grounded", "GROUNDED", " Grounded ", "3", 3]
    )
    def test_parse_accepts_codes_names_and_numbers(self, value: str | int) -> None:
        assert Maturity.parse(value) is Maturity.GROUNDED

    @pytest.mark.parametrize("value", ["L7", "expert", "", "-1", 9, True])
    def test_parse_rejects_unknown_levels(self, value: str | int) -> None:
        with pytest.raises(ValueError):
            Maturity.parse(value)

    @pytest.mark.req("TER-INT-001")
    def test_ceiling_permits_only_levels_at_or_below_it(self) -> None:
        ceiling = Maturity.EXPLAINED
        assert ceiling.permits(Maturity.MEASURED)
        assert ceiling.permits(Maturity.EXPLAINED)
        assert not ceiling.permits(Maturity.ADVISORY)
        assert not Maturity.MEASURED.permits(Maturity.ADVISORY)

    def test_title(self) -> None:
        assert Maturity.CORRECTIVE.title == "Corrective"


class TestEventIds:
    def test_ids_are_stable_hex_digests(self) -> None:
        first = make_event_id("s", "m1", 0, "reasoning")
        assert first == make_event_id("s", "m1", 0, "reasoning")
        assert len(first) == 16
        int(first, 16)

    def test_ids_differ_when_any_part_differs(self) -> None:
        base = make_event_id("s", "m1", 0, "reasoning")
        assert base != make_event_id("s", "m1", 1, "reasoning")
        assert base != make_event_id("s", "m2", 0, "reasoning")
        assert make_event_id("a", None) == make_event_id("a", "")

    def test_ids_are_frozen_across_versions(self) -> None:
        # Changing the derivation re-keys every stored finding; it must be deliberate.
        assert make_event_id("session", "uuid", 0, "reasoning") == "20e54a6dbcc334ff"


def _event(seq: int, kind: EventKind, actor: Actor, record: str) -> Event:
    return Event(
        id=make_event_id("s", record, seq),
        session_id="s",
        sequence=seq,
        kind=kind,
        actor=actor,
        text="",
        provenance=Provenance(source="t.jsonl", record_id=record),
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestSessionTrace:
    def test_generated_keeps_only_agent_output(self) -> None:
        trace = SessionTrace(
            session_id="s",
            source_format="test",
            events=(
                _event(0, EventKind.PROMPT, Actor.USER, "r1"),
                _event(1, EventKind.REASONING, Actor.ASSISTANT, "r2"),
                _event(2, EventKind.TOOL_REQUESTED, Actor.ASSISTANT, "r2"),
                _event(3, EventKind.TOOL_COMPLETED, Actor.TOOL, "r3"),
                _event(4, EventKind.RESPONSE, Actor.ASSISTANT, "r4"),
            ),
        )
        assert [e.sequence for e in trace.generated()] == [1, 2, 4]
        assert trace.schema_version == EVENT_SCHEMA_VERSION

    @pytest.mark.req("TER-SRC-002")
    def test_coverage_counts_unrecognised_records(self) -> None:
        trace = SessionTrace(
            session_id="s",
            source_format="test",
            events=(
                _event(0, EventKind.PROMPT, Actor.USER, "r1"),
                _event(1, EventKind.RESPONSE, Actor.ASSISTANT, "r2"),
                _event(2, EventKind.RESPONSE, Actor.ASSISTANT, "r2"),
            ),
            unrecognised=(
                UnrecognisedRecord(1, "summary"),
                UnrecognisedRecord(4, "summary"),
            ),
        )
        assert trace.coverage == pytest.approx(0.5)
        assert trace.unrecognised_by_type == {"summary": 2}

    def test_empty_trace_has_full_coverage(self) -> None:
        assert SessionTrace("s", "test", events=()).coverage == 1.0

    def test_token_usage_total(self) -> None:
        assert TokenUsage(1, 2, 3, 4).total == 10
