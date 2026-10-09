"""Every reported metric is recomputed from the recorded events alone.

TER-EXP-001: a session is read from its source and reported (the L1 stream
report and the L2 A3, cost included); its events are then written to a
``ter.event`` JSONL log, and the log is read back by a fresh log adapter with
nothing else: no source file, no trace, no tokenizer state. Folding the
reloaded events reproduces every reported figure exactly.

Two things in a report are not metrics of the events and are left out of
the comparison, as the requirement text says: the TER 3 ratio (computed by
TER 3 from the source transcript) and the outcome verdict (judged from
outcome evidence beside the analysis); both are planned as recorded events by
TER-EXP-002. The source's ``usage_limits`` qualify figures rather than being
figures, and come from the source, so they are left out too. The price book
is reference data shared by every session (TER-ANL-040), not session input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.gare import GareRunSource
from ter.adapters.driven.pricing import default_price_book
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import AnalyseTrace, ExplainSession
from ter.domain import EventKind, analyse_batch, explain_batch
from ter.domain.lean import build_a3
from ter.ports import SessionSource

from golden.corpus import CORPUS, REPO_ROOT

GARE = REPO_ROOT / "tests" / "fixtures" / "gare"

#: Report keys that are not recomputed from events (see the module docstring).
NOT_FROM_EVENTS = ("usage_limits", "outcome")

SESSIONS: dict[str, tuple[SessionSource, Path]] = {
    **{name: (ClaudeCodeJsonlSource(), path) for name, path in CORPUS.items()},
    "gare-failover-run": (GareRunSource(), GARE / "failover-run"),
    "gare-repair-mission": (GareRunSource(), GARE / "repair-mission"),
    "gare-real-c2ffdf8f1b09": (GareRunSource(), GARE / "runs" / "c2ffdf8f1b09"),
}


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k not in NOT_FROM_EVENTS}


@pytest.mark.req("TER-EXP-001")
@pytest.mark.parametrize("name", sorted(SESSIONS))
def test_reports_recompute_from_the_recorded_events_alone(
    name: str, tmp_path: Path
) -> None:
    source, ref = SESSIONS[name]
    prices = default_price_book()
    reported = ExplainSession(source, RegexTokenizer(), prices=prices)(ref)
    stream = AnalyseTrace(source, RegexTokenizer())(ref)
    session_id = reported.trace.session_id

    log = JsonlEventLog(tmp_path / "log")
    for event in reported.trace.events:
        log.append(event)
    events = JsonlEventLog(tmp_path / "log").events(session_id)
    assert events == reported.trace.events

    analysis = explain_batch(events, RegexTokenizer())
    intents = [e.text for e in events if e.kind is EventKind.PROMPT]
    a3 = build_a3(analysis, intents, prices=prices)

    assert _metrics(a3.as_dict()) == _metrics(reported.a3.as_dict())
    assert analysis.as_dict() == reported.analysis.as_dict()
    assert _metrics(analyse_batch(events, RegexTokenizer()).as_dict()) == _metrics(
        stream.as_dict()
    )
    # The cost is part of what was recomputed, and dated by the events.
    assert a3.cost is not None and a3.cost == reported.a3.cost
