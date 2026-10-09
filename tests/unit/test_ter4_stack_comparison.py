"""Token use and waste by language and stack, stratified (L6: TER-STK-010,
TER-STK-011), and the corpus script on synthetic data."""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from ter4_lean_builder import FAIL, PASS, Script

from ter.adapters.driven.in_memory import InMemorySessionSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import ExplainSession
from ter.application.compare_stacks import (
    CompareByStack,
    CorpusSession,
    session_measures,
)
from ter.application.explain import ExplainedSession
from ter.domain import SessionTrace
from ter.domain.stack_comparison import (
    MEASURES,
    UNLABELLED,
    Dimension,
    SessionMeasures,
    compare_strata,
)

STK10 = pytest.mark.req("TER-STK-010")
STK11 = pytest.mark.req("TER-STK-011")
ROOT = Path(__file__).resolve().parents[2]

BASE = SessionMeasures(
    language="TypeScript",
    stack="svelte+sveltekit",
    task_category="feature",
    outcome="merged",
    generated_tokens=1000,
    context_tokens=4000,
    flow_efficiency=0.8,
    unused_context=0.25,
    rework_rate=0.1,
    regeneration_rate=0.0,
    exploration_rate=0.05,
)


def many(n: int, **changes: object) -> list[SessionMeasures]:
    return [replace(BASE, **changes) for _ in range(n)]  # type: ignore[arg-type]


@STK10
class TestStratification:
    def test_strata_cross_the_group_with_task_category_and_outcome(self) -> None:
        rows = (
            many(5)
            + many(5, language="Python", stack="fastapi", generated_tokens=3000)
            + many(5, outcome="abandoned")
            + many(2, task_category="bugfix")
        )
        by_lang = compare_strata(rows, Dimension.LANGUAGE)
        keys = [
            (s.group, s.task_category, s.outcome, s.sessions) for s in by_lang.strata
        ]
        assert keys == [
            ("Python", "feature", "merged", 5),
            ("TypeScript", "bugfix", "merged", 2),
            ("TypeScript", "feature", "abandoned", 5),
            ("TypeScript", "feature", "merged", 5),
        ]
        ts = by_lang.stratum("TypeScript", "feature", "merged")
        py = by_lang.stratum("Python", "feature", "merged")
        assert ts is not None and py is not None
        assert set(ts.medians) == set(MEASURES)
        assert ts.medians["generated_tokens"] == (1000.0, 5)
        assert py.medians["generated_tokens"] == (3000.0, 5)
        assert ts.medians["rework_rate"] == (0.1, 5)
        # Only like with like: one cell holds two sufficient groups.
        assert by_lang.comparable_cells == (("feature", "merged"),)
        d = by_lang.as_dict()
        assert d["comparisons"] == [
            {
                "task_category": "feature",
                "outcome": "merged",
                "groups": ["Python", "TypeScript"],
            }
        ]
        assert d["sessions"] == 17
        by_stack = compare_strata(rows, Dimension.STACK)
        assert {s.group for s in by_stack.strata} == {"fastapi", "svelte+sveltekit"}

    def test_medians_skip_sessions_without_a_value(self) -> None:
        rows = many(3, flow_efficiency=None) + many(2, flow_efficiency=0.5)
        [s] = compare_strata(rows, Dimension.LANGUAGE).strata
        assert s.medians["flow_efficiency"] == (0.5, 2)
        rows = many(5, unused_context=None)
        [s] = compare_strata(rows, Dimension.LANGUAGE).strata
        assert s.medians["unused_context"] == (None, 0)
        assert s.as_dict()["median"]["unused_context"] is None  # type: ignore[index]

    def test_output_is_content_free(self) -> None:
        d = compare_strata(many(5), Dimension.LANGUAGE).as_dict()
        text = json.dumps(d)
        for key in ("path", "prompt", "text", "evidence"):
            assert f'"{key}"' not in text


@STK11
class TestInsufficientStrata:
    def test_a_stratum_below_the_minimum_reports_no_measure(self) -> None:
        rows = many(5) + many(4, language="Go")
        c = compare_strata(rows, Dimension.LANGUAGE, min_sessions=5)
        go = c.stratum("Go", "feature", "merged")
        ts = c.stratum("TypeScript", "feature", "merged")
        assert go is not None and ts is not None
        assert (go.sessions, go.sufficient, dict(go.medians)) == (4, False, {})
        assert ts.sufficient  # exactly the minimum is sufficient
        assert go.as_dict() == {
            "group": "Go",
            "task_category": "feature",
            "outcome": "merged",
            "sessions": 4,
            "sufficient": False,
        }
        # One sufficient group: nothing to compare against.
        assert c.comparable_cells == ()
        assert c.as_dict()["insufficient_strata"] == 1

    def test_the_minimum_is_stated_and_validated(self) -> None:
        assert (
            compare_strata(many(1), Dimension.STACK, min_sessions=1)
            .strata[0]
            .sufficient
        )
        assert compare_strata([], Dimension.STACK).as_dict()["min_sessions"] == 5
        with pytest.raises(ValueError, match="at least 1"):
            compare_strata(many(1), Dimension.STACK, min_sessions=0)
        with pytest.raises(ValueError, match="at least 1"):
            CompareByStack(lambda p, r: None, min_sessions=0)  # type: ignore[arg-type,return-value]


# --- measures of an explained session ---------------------------------------


def _rework_session(name: str) -> Script:
    s = Script(name)
    s.prompt("Fix the failing test in calc.py")
    s.read("src/calc.py")
    for _ in range(3):
        s.edit("src/calc.py", "a", "b")
        s.bash("pytest -q", FAIL)
    s.edit("src/calc.py", "b", "c")
    s.bash("pytest -q", PASS)
    s.say("Fixed.")
    return s


def _explain(script: Script) -> ExplainedSession:
    trace = SessionTrace(script.session, "script", tuple(script.events))
    source = InMemorySessionSource({script.session: trace})
    return ExplainSession(source, RegexTokenizer())(script.session)


@STK10
def test_session_measures_read_the_scorecard_and_the_waste_allocation() -> None:
    explained = _explain(_rework_session("r1"))
    m = session_measures(explained, {"task_category": "bugfix"})
    card = explained.analysis.scorecard
    assert (m.language, m.stack) == ("Python", "unknown")
    assert (m.task_category, m.outcome) == ("bugfix", UNLABELLED)
    assert m.generated_tokens == card.generated_tokens
    assert m.context_tokens == card.context_tokens
    assert m.flow_efficiency == card.flow_efficiency_tokens
    rework = sum(
        t
        for fid, t in explained.analysis.allocated_waste_tokens().items()
        if explained.analysis.finding(fid).detector == "rework_cycle"
    )
    assert rework > 0
    assert m.rework_rate == pytest.approx(rework / card.generated_tokens)
    for rate in (m.rework_rate, m.regeneration_rate, m.exploration_rate):
        assert rate is not None and 0.0 <= rate <= 1.0


@STK10
@STK11
def test_compare_by_stack_counts_failures_and_stratifies() -> None:
    scripts = {f"s{i}": _rework_session(f"s{i}") for i in range(3)}

    def explain(path: Path, repo: Path | None) -> ExplainedSession:
        if path.name == "broken":
            raise ValueError("unreadable")
        return _explain(scripts[path.name])

    sessions = [CorpusSession(Path(n), {"task_category": "bugfix"}) for n in scripts]
    report = CompareByStack(explain, min_sessions=3)(
        [*sessions, CorpusSession(Path("broken"))]
    )
    assert (report.sessions, report.analysed) == (4, 3)
    assert report.errors == {"ValueError": 1}
    [s] = report.by_language.strata
    assert (s.group, s.task_category, s.outcome, s.sessions, s.sufficient) == (
        "Python",
        "bugfix",
        UNLABELLED,
        3,
        True,
    )
    assert report.as_dict()["schema"] == "ter.corpus-by-stack/1"


# --- the corpus script --------------------------------------------------------


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "corpus_by_stack", ROOT / "scripts" / "corpus_by_stack.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@STK10
@STK11
def test_the_script_compares_an_imported_corpus(tmp_path: Path) -> None:
    sample = ROOT / "sample_sessions" / "example_session.jsonl"
    corpus = tmp_path / "corpus"
    entries = []
    for i in range(3):
        rel = f"sessions/p/s{i}.jsonl"
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(sample, corpus / rel)
        entries.append(
            {"file": rel, "labels": {"task_category": "feature", "outcome": "merged"}}
        )
    entries.append({"file": "sessions/p/missing.jsonl", "load_error": "gone"})
    (corpus / "manifest.json").write_text(json.dumps({"sessions": entries}))
    out = tmp_path / "by-stack.json"
    assert _script().main(["corpus", str(corpus), "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert (doc["sessions"], doc["analysed"]) == (3, 3)
    [s] = doc["by_language"]["strata"]
    assert s == {
        "group": "Python",
        "task_category": "feature",
        "outcome": "merged",
        "sessions": 3,
        "sufficient": False,
    }
    assert doc["by_stack"]["strata"][0]["group"] == "unknown"
    assert "example" not in out.read_text()  # no path or name reaches the output


@STK10
def test_the_script_reads_raw_transcripts_and_labels(tmp_path: Path) -> None:
    sample = ROOT / "sample_sessions" / "example_session.jsonl"
    projects = tmp_path / "projects"
    (projects / "proj").mkdir(parents=True)
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_id", "task_category", "outcome"])
        for i in range(2):
            shutil.copy(sample, projects / "proj" / f"sid{i}.jsonl")
            w.writerow([f"sid{i}", "refactor", "partial"])
        w.writerow(["no-such-session", "refactor", "partial"])
    out = tmp_path / "o.json"
    code = _script().main(
        ["raw", str(labels), str(projects), "--out", str(out), "--min-sessions", "2"]
    )
    assert code == 0
    doc = json.loads(out.read_text())
    assert doc["analysed"] == 2 and doc["errors"] == {"no transcript": 1}
    [s] = doc["by_language"]["strata"]
    assert (s["task_category"], s["outcome"], s["sufficient"]) == (
        "refactor",
        "partial",
        True,
    )
    assert set(s["median"]) == set(MEASURES)
