"""The YAML catalogue adapter, the ``ter-req`` CLI and the pytest plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ter.adapters.driven.requirements_yaml import CatalogueError, load_catalogue
from ter.adapters.driving import req_cli
from ter.adapters.driving.pytest_req import TRACE_SCHEMA

GOOD_ENTRY = """\
  - id: TER-AAA-001
    pattern: ubiquitous
    text: TER shall count tokens.
    level: L0
    status: verified
    rationale: Because.
    source_points: [1]
"""
PLANNED_ENTRY = """\
  - id: TER-AAA-002
    pattern: event-driven
    text: When a hook fires, TER shall append one event.
    level: L1
    status: planned
    rationale: Because.
"""


def _catalogue(tmp_path: Path, body: str = GOOD_ENTRY + PLANNED_ENTRY) -> Path:
    directory = tmp_path / "requirements"
    directory.mkdir()
    (directory / "all.yaml").write_text("requirements:\n" + body, encoding="utf-8")
    (directory / "vocabulary.yaml").write_text(
        "terms:\n  transcript: session\n", encoding="utf-8"
    )
    return directory


def _results(tmp_path: Path, mapping: dict[str, list[dict[str, str]]]) -> Path:
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps({"schema": TRACE_SCHEMA, "requirements": mapping}), encoding="utf-8"
    )
    return path


# -- YAML adapter ----------------------------------------------------------


def test_load_catalogue_reads_requirements_and_vocabulary(tmp_path: Path) -> None:
    catalogue = load_catalogue(_catalogue(tmp_path))
    assert catalogue.ids() == {"TER-AAA-001", "TER-AAA-002"}
    assert catalogue.vocabulary == {"transcript": "session"}
    assert catalogue.sources["TER-AAA-001"].name == "all.yaml"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("requirements: [\n", "invalid YAML"),
        ("- 1\n", "expected a mapping"),
        ("requirements:\n  - 1\n", "entry 0 is not a mapping"),
        ("requirements:\n  - id: bad\n", "does not match"),
        ("level: L9\nrequirements: []\n", "Unknown maturity level"),
        ("level: L1\nrequirements:\n" + GOOD_ENTRY, "is L0 but the file holds L1"),
    ],
)
def test_load_catalogue_rejects_bad_files(
    tmp_path: Path, content: str, message: str
) -> None:
    (tmp_path / "x.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(CatalogueError, match=message):
        load_catalogue(tmp_path)


def test_load_catalogue_rejects_bad_vocabulary(tmp_path: Path) -> None:
    (tmp_path / "vocabulary.yaml").write_text("terms: [a]\n", encoding="utf-8")
    with pytest.raises(CatalogueError, match="terms"):
        load_catalogue(tmp_path)


def test_load_catalogue_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="not found"):
        load_catalogue(tmp_path / "missing")


# -- CLI -------------------------------------------------------------------


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = req_cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_lint_passes_clean_catalogue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(capsys, "--catalogue", str(_catalogue(tmp_path)), "lint")
    assert code == 0 and "OK: 2 requirements" in out


@pytest.mark.req("TER-REQ-001")
def test_cli_lint_fails_on_grammar_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = GOOD_ENTRY.replace(
        "TER shall count tokens.", "TER should count the transcript."
    )
    code, out, _ = _run(capsys, "--catalogue", str(_catalogue(tmp_path, body)), "lint")
    assert code == 1
    assert "all.yaml: TER-AAA-001: [EARS-SHALL]" in out
    assert "[EARS-VOCAB]" in out


@pytest.mark.req("TER-REQ-003")
def test_cli_lint_scans_tests_for_unknown_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    marker = "@pytest.mark." + 'req("TER-AAA-001", "TER-ZZZ-009")'
    (tests / "test_x.py").write_text(
        f"\n{marker}\ndef test_x(): pass\n", encoding="utf-8"
    )
    assert req_cli.scan_citations(tests) == {
        "TER-AAA-001": [f"{tests.as_posix()}/test_x.py:2"],
        "TER-ZZZ-009": [f"{tests.as_posix()}/test_x.py:2"],
    }
    code, out, _ = _run(
        capsys, "--catalogue", str(_catalogue(tmp_path)), "lint", "--tests", str(tests)
    )
    assert code == 1
    assert "TER-ZZZ-009: [TRACE-UNKNOWN]" in out and "test_x.py:2" in out


@pytest.mark.req("TER-REQ-002")
def test_cli_trace_gate_fails_without_passing_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _results(
        tmp_path, {"TER-AAA-001": [{"nodeid": "t::a", "outcome": "failed"}]}
    )
    code, out, _ = _run(
        capsys,
        "--catalogue",
        str(_catalogue(tmp_path)),
        "trace",
        "--results",
        str(results),
    )
    assert code == 1
    assert "TER-AAA-001: [TRACE-FORWARD]" in out and "FAIL: gate L0" in out


def test_cli_trace_gate_passes_and_writes_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _results(
        tmp_path,
        {
            "TER-AAA-001": [{"nodeid": "t::a", "outcome": "passed"}],
            "TER-AAA-002": [{"nodeid": "t::b", "outcome": "passed"}],
        },
    )
    summary = tmp_path / "summary.md"
    code, out, _ = _run(
        capsys,
        "--catalogue",
        str(_catalogue(tmp_path)),
        "trace",
        "--results",
        str(results),
        "--summary",
        str(summary),
    )
    assert code == 0
    assert "TER-AAA-002: [TRACE-PROMOTE]" in out
    assert "OK: gate L0, 1 verified requirements checked" in out
    assert "**Gate L0 Measured: PASS**" in summary.read_text(encoding="utf-8")


def test_cli_trace_reports_unknown_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _results(
        tmp_path,
        {
            "TER-AAA-001": [{"nodeid": "t::a", "outcome": "passed"}],
            "TER-QQQ-001": [{"nodeid": "t::q", "outcome": "passed"}],
        },
    )
    code, out, _ = _run(
        capsys,
        "--catalogue",
        str(_catalogue(tmp_path)),
        "trace",
        "--results",
        str(results),
    )
    assert (
        code == 1
        and "TER-QQQ-001: [TRACE-UNKNOWN] cited but not in the catalogue: t::q" in out
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "cannot read"),
        ("[]", "expected a 'requirements' mapping"),
        ('{"requirements": {"X": 1}}', "must map to a list"),
        ('{"requirements": {"X": [{"nodeid": "a"}]}}', "without nodeid/outcome"),
    ],
)
def test_cli_trace_rejects_bad_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str, message: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    code, _, err = _run(
        capsys,
        "--catalogue",
        str(_catalogue(tmp_path)),
        "trace",
        "--results",
        str(path),
    )
    assert code == 2 and message in err


def test_cli_reports_missing_catalogue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "--catalogue", str(tmp_path / "nope"), "lint")
    assert code == 2 and "not found" in err


def test_cli_points_needs_a_points_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(capsys, "--catalogue", str(_catalogue(tmp_path)), "points")
    assert code == 2 and "no points.yaml" in err


def test_cli_report_to_stdout_and_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalogue = str(_catalogue(tmp_path))
    code, out, _ = _run(capsys, "--catalogue", catalogue, "report")
    assert code == 0 and "no test results supplied" in out
    results = _results(
        tmp_path, {"TER-AAA-001": [{"nodeid": "t::a", "outcome": "passed"}]}
    )
    target = tmp_path / "out" / "coverage.md"
    code, out, _ = _run(
        capsys,
        "--catalogue",
        catalogue,
        "report",
        "--results",
        str(results),
        "--out",
        str(target),
    )
    assert code == 0 and f"wrote {target}" in out
    assert "# TER requirements coverage" in target.read_text(encoding="utf-8")


def test_cli_rejects_bad_gate(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        req_cli.main(["trace", "--results", "x.json", "--gate", "L9"])


# -- pytest plugin -----------------------------------------------------------


@pytest.mark.req("TER-REQ-002")
def test_plugin_writes_trace_with_outcomes(pytester: pytest.Pytester) -> None:
    mark = "@pytest.mark." + "req"
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("setup")

        {mark}("TER-AAA-001")
        def test_pass(): pass

        {mark}("TER-AAA-001", "TER-AAA-002")
        def test_fail(): assert False

        {mark}("TER-AAA-003")
        @pytest.mark.skip(reason="later")
        def test_skip(): pass

        {mark}("TER-AAA-004")
        @pytest.mark.xfail(reason="known")
        def test_xfail(): assert False

        {mark}("TER-AAA-005")
        def test_setup_error(broken): pass

        def test_untagged(): pass
        """
    )
    target = pytester.path / "out" / "trace.json"
    result = pytester.runpytest(
        "-p", "ter.adapters.driving.pytest_req", f"--req-trace={target}"
    )
    result.assert_outcomes(passed=2, failed=1, skipped=1, xfailed=1, errors=1)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["schema"] == TRACE_SCHEMA
    outcomes = {
        rid: [(e["nodeid"].split("::")[-1], e["outcome"]) for e in entries]
        for rid, entries in document["requirements"].items()
    }
    assert outcomes == {
        "TER-AAA-001": [("test_fail", "failed"), ("test_pass", "passed")],
        "TER-AAA-002": [("test_fail", "failed")],
        "TER-AAA-003": [("test_skip", "skipped")],
        "TER-AAA-004": [("test_xfail", "xfailed")],
        "TER-AAA-005": [("test_setup_error", "failed")],
    }


def test_plugin_marks_deselected_tests_not_run(pytester: pytest.Pytester) -> None:
    mark = "@pytest.mark." + "req"
    pytester.makepyfile(f'import pytest\n\n{mark}("TER-AAA-001")\ndef test_a(): pass\n')
    target = pytester.path / "trace.json"
    pytester.runpytest(
        "-p", "ter.adapters.driving.pytest_req", f"--req-trace={target}", "--co"
    )
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["requirements"]["TER-AAA-001"][0]["outcome"] == "not-run"


def test_plugin_rejects_marker_without_id(pytester: pytest.Pytester) -> None:
    mark = "@pytest.mark." + "req"
    pytester.makepyfile(f"import pytest\n\n{mark}()\ndef test_a(): pass\n")
    result = pytester.runpytest(
        "-p", "ter.adapters.driving.pytest_req", "--req-trace=t.json"
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_plugin_is_inactive_without_option(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_a(): pass\n")
    result = pytester.runpytest("-p", "ter.adapters.driving.pytest_req")
    result.assert_outcomes(passed=1)
    assert not list(pytester.path.glob("*.json"))
