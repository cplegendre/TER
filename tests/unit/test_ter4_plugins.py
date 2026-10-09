"""Analysers, waste detectors and agent adapters load as plugins (TER-ARC-002).

One mechanism for all three: the capability registry (ADR 0005), keyed
``<Kind>.<name>`` in the ``ter.capabilities`` entry-point group. Entry points
are faked here, as in ``test_ter4_capabilities.py``, so nothing depends on
what is installed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import ter.bootstrap as bootstrap
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import ExplainSession, ObserveEvent
from ter.bootstrap.capabilities import (
    BUILTIN_CAPABILITIES,
    BUILTIN_ORIGIN,
    CapabilityRegistry,
    builtin_detectors,
    default_registry,
    detector_registry,
)
from ter.domain import SessionTrace, explain_batch
from ter.domain.lean import Finding, FindingKind, LeanWaste
from ter.domain.lean.detectors import DEFAULT_REGISTRY, ReworkCycle, SessionView
from ter.ports import CAPABILITY_KINDS, WasteDetectorPlugin

from tests.golden.corpus import CORPUS


@dataclass
class FakeEntry:
    name: str
    value: str
    target: Any

    def load(self) -> Any:
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target


def _registry(*entries: FakeEntry) -> CapabilityRegistry:
    return CapabilityRegistry(
        builtins={**BUILTIN_CAPABILITIES, **builtin_detectors()},
        discover=lambda: entries,
    )


@dataclass
class CountingDetector:
    """A plugin detector: finds nothing, counts the sessions it was shown."""

    id: str = "counting_plugin"
    waste: LeanWaste = LeanWaste.MOTION
    kind: FindingKind = FindingKind.WASTE
    summary: str = "Counts the sessions it sees (test plugin)."
    confidence_rule: str = "never fires"
    seen: list[int] = field(default_factory=list)

    def detect(self, view: SessionView) -> Iterable[Finding]:
        self.seen.append(len(view.steps))
        return ()


class NoDetect:
    id = "no_detect"
    waste = LeanWaste.MOTION
    kind = FindingKind.WASTE
    summary = "missing detect"
    confidence_rule = "-"


# --- waste detectors ---------------------------------------------------------


@pytest.mark.req("TER-ARC-002")
def test_every_built_in_detector_is_a_registered_capability() -> None:
    caps = default_registry().capabilities("WasteDetector")
    assert {c.name for c in caps} == {d.id for d in DEFAULT_REGISTRY}
    assert all(c.origin == BUILTIN_ORIGIN for c in caps)
    built = default_registry().create("WasteDetector", "rework_cycle")
    assert isinstance(built, WasteDetectorPlugin) and isinstance(built, ReworkCycle)
    assert "WasteDetector" in CAPABILITY_KINDS


@pytest.mark.req("TER-ARC-002")
def test_without_plugins_the_detector_set_is_the_catalogue() -> None:
    detectors = detector_registry(_registry())
    assert [d.id for d in detectors] == [d.id for d in DEFAULT_REGISTRY]


@pytest.mark.req("TER-ARC-002")
def test_an_installed_detector_runs_in_every_analysis() -> None:
    plugin = CountingDetector()
    reg = _registry(
        FakeEntry("WasteDetector.counting_plugin", "plugin:Counting", lambda: plugin)
    )
    detectors = detector_registry(reg)
    assert [d.id for d in detectors][-1] == "counting_plugin"
    assert len(detectors) == len(DEFAULT_REGISTRY) + 1

    trace = ClaudeCodeJsonlSource().read(CORPUS["lean_mix"])
    ingest = ObserveEvent(RegexTokenizer(), detectors=detectors)
    for event in trace.events:
        ingest.apply(event)
    analysis = ingest.explain(trace.session_id)
    assert plugin.seen and plugin.seen[-1] > 0
    # It is listed with the detectors that ran; finding nothing, it leaves
    # the catalogue's findings unchanged.
    assert "counting_plugin" in [d[0] for d in analysis.detectors]
    assert analysis.findings == explain_batch(trace.events, RegexTokenizer()).findings


@pytest.mark.req("TER-ARC-002")
def test_a_broken_or_clashing_detector_plugin_is_left_out_and_reported() -> None:
    reg = _registry(
        FakeEntry("WasteDetector.no_detect", "plugin:NoDetect", NoDetect),
        FakeEntry("WasteDetector.gone", "plugin:Gone", ImportError("no plugin")),
        FakeEntry(
            "WasteDetector.impostor",
            "plugin:Impostor",
            lambda: CountingDetector(id="rework_cycle"),
        ),
        FakeEntry("WasteDetector.rework_cycle", "plugin:Rework", CountingDetector),
    )
    detectors = detector_registry(reg)
    assert [d.id for d in detectors] == [d.id for d in DEFAULT_REGISTRY]
    assert isinstance(detectors.get("rework_cycle"), ReworkCycle)
    reasons = {p.key: p.reason for p in reg.problems}
    assert "missing detect" in reasons["WasteDetector.no_detect"]
    assert "failed to load" in reasons["WasteDetector.gone"]
    assert "already running" in reasons["WasteDetector.impostor"]
    assert "already registered by built-in" in reasons["WasteDetector.rework_cycle"]


def _raise_on_construct() -> CountingDetector:
    raise RuntimeError("cannot build")


@pytest.mark.req("TER-ARC-002")
def test_a_detector_plugin_whose_factory_raises_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _registry(
        FakeEntry("WasteDetector.raising", "plugin:Raising", _raise_on_construct)
    )
    detectors = detector_registry(reg)
    assert [d.id for d in detectors] == [d.id for d in DEFAULT_REGISTRY]
    reasons = {p.key: p.reason for p in reg.problems}
    assert "RuntimeError: cannot build" in reasons["WasteDetector.raising"]
    monkeypatch.setattr(bootstrap, "detector_registry", lambda: detector_registry(reg))
    assert bootstrap.make_ingest() is not None


@pytest.mark.req("TER-ARC-002")
def test_the_composition_root_explains_with_the_installed_detectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CountingDetector()
    reg = _registry(
        FakeEntry("WasteDetector.counting_plugin", "plugin:Counting", lambda: plugin)
    )
    monkeypatch.setattr(bootstrap, "detector_registry", lambda: detector_registry(reg))
    explained = bootstrap.cli_services().explain_transcript
    assert explained is not None
    explained(CORPUS["rework_loop"], "regex", "off", None)
    assert plugin.seen


# --- analysers (session scorers) ---------------------------------------------


@dataclass
class ConstantScorer:
    tokenizer: object = None
    embedder: object = None
    method: str = "constant (test plugin)"

    def score(self, ref: str | Path) -> float:
        return 0.5


@pytest.mark.req("TER-ARC-002")
@pytest.mark.parametrize("mode", ["model", "offline"])
def test_the_ter_analyser_loads_through_the_registry(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = CapabilityRegistry(
        builtins={
            k: v for k, v in BUILTIN_CAPABILITIES.items() if k != "TerScorer.ter3"
        },
        discover=lambda: (
            FakeEntry("TerScorer.ter3", "plugin:Scorer", ConstantScorer),
        ),
    )
    monkeypatch.setattr(bootstrap, "default_registry", lambda: reg)
    scorer = bootstrap.make_ter_scorer(mode)
    assert isinstance(scorer, ConstantScorer)
    if mode == "offline":  # pinned to the registry's regex and hashing adapters
        assert scorer.tokenizer is not None and scorer.embedder is not None
    assert bootstrap.make_ter_scorer("off") is None


@pytest.mark.req("TER-ARC-002")
def test_an_analyser_plugin_scores_an_explained_session() -> None:
    explained = ExplainSession(
        ClaudeCodeJsonlSource(), RegexTokenizer(), ConstantScorer()
    )(CORPUS["lean_mix"])
    ter = explained.analysis.scorecard.ter
    assert ter is not None and ter.value == 0.5
    assert ter.method == "constant (test plugin)"


# --- agent adapters (session sources) ----------------------------------------


class OtherAgentSource:
    """A session source for another coding agent's ``*.otheragent`` files."""

    format_name = "other-agent"

    @staticmethod
    def accepts(ref: str | Path) -> bool:
        return Path(ref).suffix == ".otheragent"

    def read(self, ref: str | Path) -> SessionTrace:
        trace = ClaudeCodeJsonlSource().read(CORPUS["lean_mix"])
        return SessionTrace(trace.session_id, self.format_name, trace.events)


@pytest.mark.req("TER-ARC-002")
def test_an_installed_agent_adapter_reads_the_files_it_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _registry(
        FakeEntry("SessionSource.other-agent", "plugin:Other", OtherAgentSource),
        FakeEntry("SessionSource.broken", "plugin:Broken", ImportError("gone")),
    )
    monkeypatch.setattr(bootstrap, "default_registry", lambda: reg)
    other = tmp_path / "run.otheragent"
    other.write_text("{}")
    assert isinstance(bootstrap.session_source_for(other), OtherAgentSource)
    report = bootstrap.cli_services().analyse_transcript(other, "regex")
    assert report.total_events > 0
    # Anything no adapter claims is a Claude Code transcript.
    claude = bootstrap.session_source_for(CORPUS["lean_mix"])
    assert isinstance(claude, ClaudeCodeJsonlSource)


class RaisingSource(OtherAgentSource):
    """Claims ``*.otheragent`` files but cannot be constructed."""

    def __init__(self) -> None:
        raise RuntimeError("cannot build")


@pytest.mark.req("TER-ARC-002")
def test_an_agent_adapter_whose_factory_raises_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "run.otheragent"
    other.write_text("{}")
    alone = _registry(
        FakeEntry("SessionSource.a-raising", "plugin:Raising", RaisingSource)
    )
    monkeypatch.setattr(bootstrap, "default_registry", lambda: alone)
    assert isinstance(bootstrap.session_source_for(other), ClaudeCodeJsonlSource)
    # The next source that accepts the path reads it instead.
    both = _registry(
        FakeEntry("SessionSource.a-raising", "plugin:Raising", RaisingSource),
        FakeEntry("SessionSource.other-agent", "plugin:Other", OtherAgentSource),
    )
    monkeypatch.setattr(bootstrap, "default_registry", lambda: both)
    assert isinstance(bootstrap.session_source_for(other), OtherAgentSource)


@pytest.mark.req("TER-ARC-002")
def test_the_built_in_agent_adapters_are_capabilities() -> None:
    names = default_registry().names("SessionSource")
    assert {"claude-code", "gare"} <= set(names)
