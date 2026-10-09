"""Measures recorded as events of their session (TER-EXP-002).

The record codecs: where a record sits, that its id follows its content,
that the fold reads the recorded TER 3 ratio but counts no record as
activity, that a record that cannot be read never breaks a live fold, and
that a recorded verdict is judged again from the evidence it holds.
Positive, negative and boundary cases; the end-to-end recompute over the
golden corpus is in ``tests/equivalence/test_recompute_from_events.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.domain import AnalysisEngine, analyse_batch, explain_batch
from ter.domain.events import (
    Actor,
    Event,
    EventId,
    EventKind,
    Provenance,
)
from ter.domain.lean import TerMeasure
from ter.domain.outcome import (
    AcceptanceContract,
    Check,
    CheckEvidence,
    CheckStatus,
    OutcomeEvidence,
    OutcomeFormatError,
    OutcomeVerdict,
    judge,
    recorded_verdict,
    verdict_event,
)
from ter.domain.records import (
    TER3_METRIC,
    metric_event,
    read_metric,
    record_event,
    recorded_metric,
)

pytestmark = pytest.mark.req("TER-EXP-002")

AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _event(n: int, kind: EventKind = EventKind.PROMPT, text: str = "go") -> Event:
    return Event(
        id=EventId(f"e{n}"),
        session_id="s",
        sequence=n,
        kind=kind,
        actor=Actor.USER,
        text=text,
        provenance=Provenance("test", f"r{n}"),
        timestamp=AT,
    )


SESSION = (_event(1), _event(2, EventKind.RESPONSE, "done"), _event(3))


def _verdict(*statuses: CheckStatus) -> OutcomeVerdict:
    evidence = OutcomeEvidence(
        "run-1",
        "junit",
        tuple(
            CheckEvidence(f"t::c{n}", s, "results.xml", seconds=0.5)
            for n, s in enumerate(statuses)
        ),
    )
    contract = AcceptanceContract(
        "two checks", (Check("t::c0"), Check("t::c1", required=False))
    )
    return judge(evidence, contract)


class TestMetricRecords:
    def test_a_record_follows_the_last_event_of_its_session(self) -> None:
        record = metric_event(SESSION, TER3_METRIC, 0.4321, "offline", offset=2)
        assert record.kind is EventKind.METRIC_RECORDED
        assert (record.session_id, record.sequence) == ("s", 5)
        assert record.actor is Actor.SYSTEM and record.timestamp == AT
        assert record.provenance.source == "ter"
        assert read_metric(record) is not None

    def test_the_id_follows_the_content(self) -> None:
        a = metric_event(SESSION, TER3_METRIC, 0.5, "offline")
        assert metric_event(SESSION, TER3_METRIC, 0.5, "offline").id == a.id
        assert metric_event(SESSION, TER3_METRIC, 0.6, "offline").id != a.id

    def test_only_record_kinds_and_a_session_make_a_record(self) -> None:
        with pytest.raises(ValueError, match="not a record kind"):
            record_event(SESSION, EventKind.OUTCOME_RECORDED, {})
        with pytest.raises(ValueError, match="session"):
            metric_event((), TER3_METRIC, 0.5, "offline")

    def test_the_fold_reads_the_ratio_and_counts_no_record(self) -> None:
        record = metric_event(SESSION, TER3_METRIC, 0.4321, "offline")
        events = (*SESSION, record)
        analysis = explain_batch(events, RegexTokenizer())
        assert analysis.scorecard.ter == TerMeasure(0.4321, "offline")
        assert (
            analyse_batch(events, RegexTokenizer()).as_dict()
            == analyse_batch(SESSION, RegexTokenizer()).as_dict()
        )
        # A ratio passed in explicitly wins over the recorded one.
        engine = AnalysisEngine(RegexTokenizer())
        engine.apply_all(events)
        given = TerMeasure(0.9, "model")
        assert engine.explain(ter=given).scorecard.ter == given

    def test_the_last_recorded_ratio_wins(self) -> None:
        old = metric_event(SESSION, TER3_METRIC, 0.1, "offline")
        new = metric_event(SESSION, TER3_METRIC, 0.2, "offline", offset=2)
        assert explain_batch(
            (*SESSION, old, new), RegexTokenizer()
        ).scorecard.ter == TerMeasure(0.2, "offline")
        metric = recorded_metric((*SESSION, old, new), TER3_METRIC)
        assert metric is not None and metric.value == 0.2

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "[1]",
            '{"name": "ter3", "value": "high", "method": "x"}',
            '{"name": "ter3", "value": true, "method": "x"}',
            '{"name": "ter3", "value": 0.5}',
        ],
    )
    def test_a_record_that_cannot_be_read_is_ignored_not_fatal(self, text: str) -> None:
        bad = replace(
            metric_event(SESSION, TER3_METRIC, 0.5, "x"), text=text, id=EventId("bad")
        )
        assert read_metric(bad) is None
        assert explain_batch((*SESSION, bad), RegexTokenizer()).scorecard.ter is None

    def test_another_metric_is_not_the_ter3_ratio(self) -> None:
        other = metric_event(SESSION, "other", 0.5, "x")
        assert explain_batch((*SESSION, other), RegexTokenizer()).scorecard.ter is None
        assert read_metric(SESSION[0]) is None


class TestVerdictRecords:
    @pytest.mark.parametrize(
        "statuses",
        [
            (CheckStatus.PASSED, CheckStatus.FAILED),
            (CheckStatus.FAILED, CheckStatus.PASSED),
            (CheckStatus.SKIPPED,),
        ],
    )
    def test_a_recorded_verdict_is_judged_again_to_the_same_verdict(
        self, statuses: tuple[CheckStatus, ...]
    ) -> None:
        verdict = _verdict(*statuses)
        record = verdict_event(SESSION, verdict)
        assert record.kind is EventKind.VERDICT_RECORDED
        assert recorded_verdict((*SESSION, record)) == verdict

    def test_unlisted_and_repeated_evidence_survive(self) -> None:
        evidence = OutcomeEvidence(
            "run",
            "junit",
            (
                CheckEvidence("a", CheckStatus.FAILED, "x"),
                CheckEvidence("extra", CheckStatus.PASSED, "x", "note"),
                CheckEvidence("a", CheckStatus.PASSED, "x"),
            ),
        )
        verdict = judge(evidence, AcceptanceContract("a only", (Check("a"),)))
        assert recorded_verdict([verdict_event(SESSION, verdict)]) == verdict

    def test_no_record_is_no_verdict(self) -> None:
        assert recorded_verdict(SESSION) is None

    def test_a_record_its_evidence_does_not_support_is_refused(self) -> None:
        record = verdict_event(SESSION, _verdict(CheckStatus.PASSED))
        forged = replace(record, text=record.text.replace('"accepted"', '"rejected"'))
        with pytest.raises(OutcomeFormatError, match="judged 'accepted'"):
            recorded_verdict([forged])

    @pytest.mark.parametrize(
        "text",
        [
            "nope",
            '{"verdict": "accepted"}',
            '{"verdict": "accepted", "contract": {"name": "c"}, "evidence": []}',
            '{"verdict": "accepted", "run": "r", "source": "s", '
            '"contract": {"name": "c", "checks": [{"id": "a"}]}, '
            '"evidence": [{"check": "a", "status": "green", "source": "s"}]}',
        ],
    )
    def test_an_unreadable_record_raises(self, text: str) -> None:
        record = replace(
            verdict_event(SESSION, _verdict(CheckStatus.PASSED)), text=text
        )
        with pytest.raises(OutcomeFormatError, match="verdict record"):
            recorded_verdict([record])

    def test_the_fold_counts_no_verdict_record(self) -> None:
        record = verdict_event(SESSION, _verdict(CheckStatus.PASSED))
        assert (
            explain_batch((*SESSION, record), RegexTokenizer()).as_dict()
            == explain_batch(SESSION, RegexTokenizer()).as_dict()
        )
