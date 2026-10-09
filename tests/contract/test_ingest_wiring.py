"""Every observation reaches analysis through the ``EventIngest`` port (TER-OBS-001).

The composition root (``ter.bootstrap``) builds one ``EventIngest`` per
recorded session (``make_ingest``) and one per hook process
(``make_recorder``). These tests replace both with a spy that records every
event applied and delegates to the real use case, then drive each path the
CLI offers:

* a recorded Claude Code transcript (``SessionSource.claude-code``);
* a recorded GARE run (``SessionSource.gare``);
* a replayed event log;
* a live Claude Code hook, read from stdin by ``python -m ter hook``.

Each path must apply every event of its recording, in order, to the spy, and
return exactly what the spy reports: no path may analyse around the port.
A static check backs this up: outside the domain, nothing folds events into
an ``AnalysisEngine`` except the ``ObserveEvent`` use case.
"""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pytest

import ter.bootstrap as bootstrap
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.gare import GareRunSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import main as cli_main
from ter.application import ObserveEvent
from ter.domain import Event, analyse_batch, explain_batch
from ter.domain.events import EVENT_SCHEMA_VERSION, SessionTrace
from ter.domain.lean import LeanAnalysis, TerMeasure
from ter.domain.stream import Signals, StreamReport
from ter.ports import EventIngest

from golden.corpus import CORPUS, REPO_ROOT

GARE_RUNS = REPO_ROOT / "tests" / "fixtures" / "gare"
HOOKS = REPO_ROOT / "tests" / "fixtures" / "hooks"
SRC = REPO_ROOT / "src" / "ter"


class SpyIngest:
    """An ``EventIngest`` that records what passes through it."""

    def __init__(self, inner: EventIngest) -> None:
        self.inner = inner
        self.applied: list[Event] = []
        self.reports: list[StreamReport] = []
        self.explanations: list[LeanAnalysis] = []

    def apply(self, event: Event) -> Signals:
        self.applied.append(event)
        return self.inner.apply(event)

    def report(self, session_id: str) -> StreamReport:
        report = self.inner.report(session_id)
        self.reports.append(report)
        return report

    def explain(
        self, session_id: str, *, ter: TerMeasure | None = None
    ) -> LeanAnalysis:
        analysis = self.inner.explain(session_id, ter=ter)
        self.explanations.append(analysis)
        return analysis


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> list[SpyIngest]:
    """Every ``EventIngest`` the composition root builds, as spies."""
    built: list[SpyIngest] = []
    real_ingest = bootstrap.make_ingest
    real_recorder = bootstrap.make_recorder

    def make_ingest(tokenizer: str = "regex") -> EventIngest:
        built.append(SpyIngest(real_ingest(tokenizer)))
        return built[-1]

    def make_recorder(directory: Path) -> EventIngest:
        built.append(SpyIngest(real_recorder(directory)))
        return built[-1]

    monkeypatch.setattr(bootstrap, "make_ingest", make_ingest)
    monkeypatch.setattr(bootstrap, "make_recorder", make_recorder)
    return built


def _gare_runs() -> list[Path]:
    return sorted(p for p in GARE_RUNS.iterdir() if p.is_dir())


def _assert_ter_events(trace: SessionTrace) -> None:
    assert trace.schema_version == EVENT_SCHEMA_VERSION
    assert trace.events and all(isinstance(e, Event) for e in trace.events)


def test_the_spy_satisfies_the_port() -> None:
    assert isinstance(SpyIngest(ObserveEvent(RegexTokenizer())), EventIngest)


@pytest.mark.req("TER-OBS-001")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a_claude_code_transcript_enters_through_event_ingest(
    name: str, spies: list[SpyIngest]
) -> None:
    path = CORPUS[name]
    trace = ClaudeCodeJsonlSource().read(path)
    _assert_ter_events(trace)

    report = bootstrap.cli_services().analyse_transcript(path, "regex")

    assert len(spies) == 1
    assert spies[0].applied == list(trace.events)
    assert spies[0].reports == [report]
    assert report == analyse_batch(trace.events, RegexTokenizer())


@pytest.mark.req("TER-OBS-001")
@pytest.mark.parametrize("run", _gare_runs(), ids=lambda p: p.name)
def test_a_gare_run_enters_through_event_ingest(
    run: Path, spies: list[SpyIngest]
) -> None:
    assert GareRunSource.accepts(run)
    trace = GareRunSource().read(run)
    _assert_ter_events(trace)

    report = bootstrap.cli_services().analyse_transcript(run, "regex")

    assert len(spies) == 1
    assert spies[0].applied == list(trace.events)
    assert report.total_events == spies[0].reports[0].total_events
    assert report.usage_limits == trace.usage_limits


@pytest.mark.req("TER-OBS-001")
@pytest.mark.parametrize(
    "ref",
    [CORPUS["lean_mix"], *_gare_runs()],
    ids=lambda p: p.name,
)
def test_an_explained_recording_enters_through_event_ingest(
    ref: Path, spies: list[SpyIngest]
) -> None:
    explained = bootstrap.cli_services().explain_transcript
    assert explained is not None
    result = explained(ref, "regex", "off", None)

    assert len(spies) == 1
    assert spies[0].applied == list(result.trace.events)
    assert spies[0].explanations == [result.analysis]
    assert result.analysis == explain_batch(result.trace.events, RegexTokenizer())


@pytest.mark.req("TER-OBS-001")
def test_an_event_log_replay_enters_through_event_ingest(
    tmp_path: Path, spies: list[SpyIngest]
) -> None:
    trace = ClaudeCodeJsonlSource().read(CORPUS["rework_loop"])
    log = JsonlEventLog(tmp_path)
    for event in trace.events:
        log.append(event)

    report = bootstrap.cli_services().analyse_log(tmp_path, trace.session_id, "regex")

    assert len(spies) == 1
    assert spies[0].applied == list(trace.events)
    assert spies[0].reports == [report]
    assert report == analyse_batch(trace.events, RegexTokenizer())


@pytest.mark.req("TER-OBS-001")
def test_live_hooks_enter_through_event_ingest(
    tmp_path: Path, spies: list[SpyIngest]
) -> None:
    """``python -m ter hook`` applies each hook's events to the recorder, and
    the session it recorded is analysed by replaying the log through
    ``make_ingest`` too."""
    services = bootstrap.cli_services()
    order = ("user_prompt_submit", "pre_tool_use_read", "post_tool_use_read", "stop")
    for name in order:
        raw = (HOOKS / f"{name}.json").read_text(encoding="utf-8")
        code = cli_main(
            ["hook", "--event-log", str(tmp_path)],
            services,
            stdin=io.StringIO(raw),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        assert code == 0
    recorders = list(spies)
    assert len(recorders) == len(order)  # one per hook process
    live = [e for spy in recorders for e in spy.applied]
    assert live, "hooks applied no events"
    session = json.loads((HOOKS / "user_prompt_submit.json").read_text())["session_id"]
    assert JsonlEventLog(tmp_path).events(session) == tuple(live)

    report = services.analyse_log(tmp_path, session, "regex")
    replay = spies[len(order) :]
    assert len(replay) == 1 and replay[0].applied == live
    # A PostToolUse repeats its PreToolUse request; analysis applies each id once.
    assert report.total_events == len({e.id for e in live})


def _calls(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.req("TER-OBS-001")
def test_nothing_outside_the_domain_analyses_around_event_ingest() -> None:
    """Only the domain folds events, and only ``ObserveEvent`` drives it."""
    around = {"analyse_batch", "explain_batch", "AnalysisEngine", "LeanAnalyser"}
    offenders: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        if relative.parts[0] == "domain":
            continue
        used = _calls(ast.parse(path.read_text(encoding="utf-8"))) & around
        if used:
            offenders[str(relative)] = used
    assert offenders == {}
