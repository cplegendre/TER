"""Importing real sessions into a redacted corpus (issue #34)."""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code.corpus import (
    CORPUS_SCHEMA,
    import_corpus,
    read_labels,
)
from ter.adapters.driven.claude_code.redaction import RedactionPolicy
from ter.bootstrap import cli_services
from ter.adapters.driving.cli import main
from tests.golden.corpus import CORPUS

SESSION = "0b6f8c4e-2d1a-4f7e-9c3b-5a8d7e6f1a2b"
CWD = "/home/leigh/work/acme-billing"
PROJECT = "-home-leigh-work-acme-billing"
SECRET = "AKIAABCDEFGHIJKLMNOP"


def records(session: str = SESSION) -> list[dict[str, Any]]:
    base = {"sessionId": session, "cwd": CWD}
    return [
        {
            **base,
            "type": "user",
            "uuid": "u1",
            "timestamp": "2026-10-01T09:00:00Z",
            "message": {"role": "user", "content": f"deploy with {SECRET} please"},
        },
        {
            **base,
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "timestamp": "2026-10-01T09:00:05Z",
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "model": "claude-test",
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": f"{CWD}/billing.py"},
                    }
                ],
            },
        },
        {
            **base,
            "type": "user",
            "uuid": "r1",
            "parentUuid": "a1",
            "timestamp": "2026-10-01T09:00:06Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "def charge(card):\n    return proprietary(card)",
                    }
                ],
            },
        },
    ]


def write_session(folder: Path, name: str = SESSION, **kwargs: Any) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records(**kwargs)), encoding="utf-8"
    )
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    write_session(root / PROJECT)
    # A subagent transcript in a subfolder, as Claude Code keeps them.
    write_session(root / PROJECT / SESSION / "subagents", "agent-a1b2c3")
    return root


def all_text(folder: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*")) if p.is_file()
    )


@pytest.mark.req("TER-SRC-006")
def test_nothing_unredacted_reaches_the_corpus(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "corpus"
    import_corpus([source], out)

    written = all_text(out)
    for leak in (SECRET, "leigh", "acme", "proprietary", PROJECT):
        assert leak not in written
    # The raw files are only read.
    assert SECRET in all_text(source)


@pytest.mark.req("TER-SRC-023")
def test_manifest_lists_every_session_with_dates_coverage_and_redactions(
    source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "corpus"
    result = import_corpus([source], out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == CORPUS_SCHEMA
    assert manifest["policy"] == {
        "keep_tools": [],
        "max_tool_output": 2000,
        "quote_file_contents": False,
    }
    entries = manifest["sessions"]
    assert len(entries) == len(result.sessions) == 2
    main_entry = next(e for e in entries if "subagents" not in e["file"])
    assert main_entry["session_id"] == SESSION
    assert main_entry["records"] == 3
    assert main_entry["first_timestamp"] == "2026-10-01T09:00:00Z"
    assert main_entry["last_timestamp"] == "2026-10-01T09:00:06Z"
    assert main_entry["coverage"] == 1.0
    assert main_entry["redactions"]["aws-access-key"] == 1
    assert main_entry["redactions"]["file-content"] == 1
    assert main_entry["file"] == f"sessions/{main_entry['project']}/{SESSION}.jsonl"
    assert main_entry["load_error"] is None
    for entry in entries:
        assert (out / entry["file"]).is_file()
        report = out / "reports" / Path(entry["file"]).relative_to("sessions")
        assert report.with_suffix(".json").is_file()


def test_redaction_report_locates_each_removal_without_the_value(
    source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "corpus"
    import_corpus([source], out)
    reports = sorted((out / "reports").rglob("*.json"))
    report = json.loads(reports[0].read_text(encoding="utf-8"))

    assert report["schema"] == "ter.redaction-report/1"
    kinds = {(r["kind"], r["line"]) for r in report["redactions"]}
    assert ("aws-access-key", 1) in kinds and ("file-content", 3) in kinds
    assert SECRET not in json.dumps(report)


@pytest.mark.req("TER-SRC-021")
def test_reimporting_into_the_same_corpus_keeps_pseudonyms(
    source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "corpus"
    first = import_corpus([source], out)
    second = import_corpus([source], out)
    assert [s.file for s in first.sessions] == [s.file for s in second.sessions]
    assert {s.project for s in first.sessions} == {s.project for s in second.sessions}

    other = import_corpus([source], tmp_path / "other")
    assert {s.project for s in other.sessions} != {s.project for s in first.sessions}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_the_salt_is_private(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "corpus"
    import_corpus([source], out)
    assert stat.S_IMODE((out / ".salt").stat().st_mode) == 0o600


def test_non_id_file_names_are_hashed(tmp_path: Path) -> None:
    src = tmp_path / "src"
    write_session(src / PROJECT, "leigh-acme-notes")
    result = import_corpus([src], tmp_path / "corpus")
    assert "leigh" not in result.sessions[0].file
    assert result.sessions[0].file.endswith(".jsonl")


def test_a_single_file_source_is_imported(tmp_path: Path) -> None:
    path = write_session(tmp_path / "src" / PROJECT)
    result = import_corpus([path], tmp_path / "corpus")
    assert [s.session_id for s in result.sessions] == [SESSION]
    assert PROJECT not in result.sessions[0].project


def test_unparsable_lines_are_skipped_and_counted(tmp_path: Path) -> None:
    path = write_session(tmp_path / "src" / PROJECT)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f'{{"half a record with {SECRET}\n[1, 2]\n\n')
    out = tmp_path / "corpus"
    result = import_corpus([path], out)
    assert result.sessions[0].unparsed_lines == 2
    assert result.sessions[0].records == 3
    assert SECRET not in all_text(out)


def test_a_session_the_source_cannot_load_is_recorded_not_raised(
    tmp_path: Path,
) -> None:
    path = tmp_path / "src" / "broken.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type": "user", "message": "no uuid"}\n', encoding="utf-8")
    result = import_corpus([path], tmp_path / "corpus")
    (session,) = result.sessions
    assert session.load_error is not None
    assert result.load_failures == (session,)
    assert session.coverage is None


def test_policy_reaches_the_redactor_and_the_manifest(
    source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "corpus"
    policy = RedactionPolicy(quote_file_contents=True, keep_tools=frozenset({"Bash"}))
    import_corpus([source], out, policy=policy)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["policy"]["quote_file_contents"] is True
    assert manifest["policy"]["keep_tools"] == ["Bash"]
    assert "proprietary" in all_text(out / "sessions")


@pytest.mark.req("TER-SRC-023")
def test_labels_are_joined_by_session_id(source: Path, tmp_path: Path) -> None:
    csv = tmp_path / "labels.csv"
    csv.write_text(
        "session_id,task_category,task,outcome,rating,licence\n"
        f"{SESSION},bugfix,fix rounding,merged,4,MIT\n"
        "missing-session,feature,,unknown,,\n",
        encoding="utf-8",
    )
    result = import_corpus([source], tmp_path / "corpus", labels=read_labels(csv))
    labelled = [s for s in result.sessions if s.labels]
    assert labelled and all(
        s.labels
        == {
            "task_category": "bugfix",
            "task": "fix rounding",
            "outcome": "merged",
            "rating": "4",
            "licence": "MIT",
        }
        for s in labelled
    )
    assert result.unknown_labels == ("missing-session",)
    assert result.unlabelled == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("task,outcome\nx,merged\n", "no session_id column"),
        ("session_id,outcome\na,shipped\n", "outcome 'shipped'"),
        ("session_id,rating\na,6\n", "rating '6'"),
        ("session_id,rating\na,1\na,2\n", "labelled twice"),
        ("session_id,rating\n,1\n", "empty session_id"),
    ],
)
def test_bad_label_files_are_refused(tmp_path: Path, body: str, message: str) -> None:
    csv = tmp_path / "labels.csv"
    csv.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_labels(csv)


def test_golden_sessions_import_with_their_coverage(tmp_path: Path) -> None:
    src = tmp_path / "src" / PROJECT
    src.mkdir(parents=True)
    for name, path in CORPUS.items():
        (src / f"{name}.jsonl").write_bytes(path.read_bytes())
    result = import_corpus([tmp_path / "src"], tmp_path / "corpus")
    assert len(result.sessions) == len(CORPUS)
    assert result.load_failures == ()
    assert all(s.coverage is not None and s.coverage > 0.9 for s in result.sessions)


# -- command line -----------------------------------------------------------


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_imports_and_summarises(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "corpus"
    code, text, err = run("corpus", "import", str(source), "--out", str(out))
    assert (code, err) == (0, "")
    assert "2 session(s)" in text and "aws-access-key 2" in text
    assert "1 session id(s) have no labels" in text  # the subagent shares it
    assert (out / "manifest.json").is_file()


def test_cli_passes_the_policy(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "corpus"
    code, _, _ = run(
        "corpus",
        "import",
        str(source),
        "--out",
        str(out),
        "--max-tool-output",
        "5",
        "--keep-tool",
        "Bash",
        "--keep-tool",
        "Grep",
        "--quote-files",
    )
    assert code == 0
    policy = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["policy"]
    assert policy == {
        "keep_tools": ["Bash", "Grep"],
        "max_tool_output": 5,
        "quote_file_contents": True,
    }


@pytest.mark.parametrize(
    "case", ["missing source", "out inside source", "missing labels", "bad labels"]
)
def test_cli_refuses_bad_input_and_writes_nothing(
    case: str, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "corpus"
    argv = ["corpus", "import", str(source), "--out", str(out)]
    if case == "missing source":
        argv[2] = str(tmp_path / "nowhere")
    elif case == "out inside source":
        out = source / "corpus"
        argv[4] = str(out)
    elif case == "missing labels":
        argv += ["--labels", str(tmp_path / "none.csv")]
    else:
        bad = tmp_path / "labels.csv"
        bad.write_text("session_id,rating\na,9\n", encoding="utf-8")
        argv += ["--labels", str(bad)]
    code, _, err = run(*argv)
    assert code == 2 and err
    assert not (out / "manifest.json").exists()
