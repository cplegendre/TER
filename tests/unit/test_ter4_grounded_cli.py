"""End to end: ``python -m ter explain|a3 SESSION --repo DIR`` on a synthetic
Claude Code transcript and the synthetic repository it worked in, at the
session's start commit (TER-EVD-006, TER-EVD-007)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from ter4_shop_repo import SHOP, at, shop_repo

from ter import bootstrap
from ter.adapters.driving.cli import main


class Transcript:
    """Writes a minimal Claude Code JSONL transcript."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []
        self._tools = 0

    def _record(self, kind: str, message: dict[str, Any]) -> None:
        n = len(self.lines)
        self.lines.append(
            {
                "type": kind,
                "uuid": f"u{n}",
                "parentUuid": f"u{n - 1}" if n else None,
                "sessionId": "grounded-session",
                "timestamp": f"2026-01-15T10:{n // 60:02d}:{n % 60:02d}.000Z",
                "message": message,
            }
        )

    def prompt(self, text: str) -> None:
        self._record(
            "user", {"role": "user", "content": [{"type": "text", "text": text}]}
        )

    def tool(self, name: str, arguments: dict[str, Any], output: str) -> None:
        self._tools += 1
        tool_id = f"tool-{self._tools}"
        self._record(
            "assistant",
            {
                "id": f"m{self._tools}",
                "role": "assistant",
                "model": "claude-sonnet-demo",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": arguments,
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        self._record(
            "user",
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "content": output}
                ],
            },
        )

    def say(self, text: str) -> None:
        self._record(
            "assistant",
            {
                "id": f"r{len(self.lines)}",
                "role": "assistant",
                "model": "claude-sonnet-demo",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def write(self, path: Path) -> Path:
        path.write_text(
            "".join(json.dumps(line) + "\n" for line in self.lines), encoding="utf-8"
        )
        return path


def session(tmp_path: Path) -> Path:
    t = Transcript()
    t.prompt("Fix the rounding in pricing.py so totals are exact")
    pricing = "src/app/domain/pricing.py"
    t.tool("Read", {"file_path": at(pricing)}, SHOP[pricing])
    t.tool(
        "Edit",
        {"file_path": at(pricing), "old_string": "* 1.2", "new_string": "* 12 / 10"},
        "updated",
    )
    t.tool(
        "Edit",
        {
            "file_path": at("src/app/reports/summary.py"),
            "old_string": "len(rows)",
            "new_string": "len(list(rows))",
        },
        "updated",
    )
    t.tool(
        "Edit",
        {
            "file_path": at("src/app/domain/model.py"),
            "old_string": "class Order:",
            "new_string": "from app.service.checkout import checkout\n\n\nclass Order:",
        },
        "updated",
    )
    t.tool("Bash", {"command": "pytest -q"}, "4 passed in 0.10s")
    t.say("Fixed the rounding.")
    return t.write(tmp_path / "session.jsonl")


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.req("TER-EVD-006")
@pytest.mark.req("TER-EVD-007")
def test_explain_with_a_repository_reports_surfaces_and_violations(
    tmp_path: Path,
) -> None:
    repo = shop_repo(tmp_path / "shop")
    code, out, err = run(
        ["explain", str(session(tmp_path)), "--repo", str(repo), "--json"]
    )
    assert code == 0, err
    analysis = json.loads(out)
    [surface] = analysis["change_surfaces"]
    assert surface["seeds"] == ["src/app/domain/pricing.py"]
    assert [e["placement"] for e in surface["edits"]] == [
        "inside",
        "unrelated",
        "inside",
    ]
    assert analysis["repository"]["engine"] == "syntax"
    assert analysis["repository"]["contracts"] == ["layers", "pure-domain"]
    by = {f["detector"]: f for f in analysis["findings"]}
    assert by["unrelated_modification"]["confidence"] == 0.8
    assert by["unrelated_modification"]["subject"] == "src/app/reports/summary.py"
    assert by["boundary_violation"]["subject"] == "layers"
    assert by["boundary_violation"]["kind"] == "risk"


@pytest.mark.req("TER-EVD-006")
def test_explain_text_and_a3_with_a_repository(tmp_path: Path) -> None:
    repo = shop_repo(tmp_path / "shop")
    path = str(session(tmp_path))
    code, out, _ = run(["explain", path, "--repo", str(repo)])
    assert code == 0
    assert (
        "change surface   1 task(s); edits: 2 inside, 0 expansion, 1 unrelated, "
        "0 outside repository; 2 contract(s) from pyproject.toml"
    ) in out
    code, out, _ = run(["a3", path, "--repo", str(repo), "--ter", "off", "--json"])
    assert code == 0
    a3 = json.loads(out)
    detectors = {c["detector"] for c in a3["countermeasures"]}
    assert {"unrelated_modification", "boundary_violation"} <= detectors


@pytest.mark.req("TER-EVD-006")
def test_without_a_repository_explain_is_unchanged(tmp_path: Path) -> None:
    code, out, _ = run(["explain", str(session(tmp_path)), "--json"])
    assert code == 0
    analysis = json.loads(out)
    assert "change_surfaces" not in analysis and "repository" not in analysis
    assert not {"unrelated_modification", "boundary_violation"} & {
        f["detector"] for f in analysis["findings"]
    }


def test_a_missing_or_unusable_repository_is_refused(tmp_path: Path) -> None:
    path = str(session(tmp_path))
    code, _, err = run(["explain", path, "--repo", str(tmp_path / "nope")])
    assert code == 2 and "No such repository directory" in err
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, err = run(["explain", path, "--repo", str(plain), "--repo-engine", "git"])
    assert code == 2 and "Cannot read repository" in err
    code, _, err = run(["explain", path, "--repo", str(plain), "--repo-engine", "nope"])
    assert code == 2 and "Cannot read repository" in err


def test_unreadable_contracts_are_a_warning(tmp_path: Path) -> None:
    files = dict(SHOP)
    files["pyproject.toml"] = "[tool.importlinter\n"
    repo = shop_repo(tmp_path / "shop", files)
    code, out, err = run(["explain", str(session(tmp_path)), "--repo", str(repo)])
    assert code == 0
    assert "Architecture contracts not checked: pyproject.toml" in err
    assert "no contracts" in out
