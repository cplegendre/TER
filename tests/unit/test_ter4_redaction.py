"""Redacting Claude Code records before they enter a corpus (issue #34)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.claude_code.redaction import (
    SECRET_PATTERNS,
    RedactionPolicy,
    Redactor,
)
from tests.golden.corpus import CORPUS

CWD = "/home/leigh/work/acme-billing"


def user(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": "u1",
        "sessionId": "s1",
        "cwd": CWD,
        "timestamp": "2026-10-08T12:00:00Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        **extra,
    }


def tool_use(call: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": f"a-{call}",
        "sessionId": "s1",
        "cwd": CWD,
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-test",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "tool_use", "id": call, "name": name, "input": arguments}
            ],
        },
    }


def tool_result(call: str, content: object) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": f"r-{call}",
        "sessionId": "s1",
        "cwd": CWD,
        "toolUseResult": {"stdout": "copy of the output"},
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call, "content": content}
            ],
        },
    }


def text_of(record: dict[str, Any]) -> str:
    return json.dumps(record)


SECRETS = {
    "private-key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    "aws-access-key": "AKIAABCDEFGHIJKLMNOP",
    "github-token": "ghp_" + "a1" * 18,
    "api-key": "sk-ant-" + "x9" * 12,
    "slack-token": "xoxb-1234567890-abcdef",
    "jwt": "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJl",
    "bearer-token": "Bearer abcdefghijklmnop",
    "assigned-secret": "password=hunter2hunter2",
    "email": "leigh@example.org",
    "ipv4": "10.20.30.40",
}


def test_every_secret_pattern_has_a_case() -> None:
    assert set(SECRETS) == {kind for kind, _ in SECRET_PATTERNS}


@pytest.mark.req("TER-SRC-020")
@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_secret_kind_is_replaced_and_logged_without_its_value(kind: str) -> None:
    secret = SECRETS[kind]
    redactor = Redactor()
    out = redactor.redact(user(f"use {secret} now"), line=3)

    text = out["message"]["content"][0]["text"]
    assert f"[REDACTED:{kind}]" in text
    assert secret not in text_of(out)
    logged = [r for r in redactor.redactions if r.kind == kind]
    assert logged and logged[0].line == 3
    assert logged[0].path == "message.content[0].text"
    assert secret not in repr(redactor.redactions)


@pytest.mark.req("TER-SRC-020")
def test_assigned_secret_keeps_the_name_so_code_still_reads() -> None:
    out = Redactor().redact(user('API_KEY = "abcdef123456"'))
    assert (
        out["message"]["content"][0]["text"] == 'API_KEY = "[REDACTED:assigned-secret]"'
    )


@pytest.mark.req("TER-SRC-020")
@pytest.mark.parametrize(
    "text",
    [
        "version 1.2.3 of the package",  # not an IPv4 address
        "999.1.1.1 is not an address",
        "password: short",  # under six characters
        "sk-short",
        "the task is Bearer of bad news",
        "decorator @retry on line 4",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    redactor = Redactor()
    record = user(text, cwd=None)
    assert redactor.redact(record)["message"]["content"][0]["text"] == text
    assert redactor.redactions == []


@pytest.mark.req("TER-SRC-021")
def test_working_directory_becomes_one_pseudonym_everywhere() -> None:
    redactor = Redactor(RedactionPolicy(salt="s"))
    records = [
        user(f"look at {CWD}/src/app.py"),
        tool_use("c1", "Read", file_path=f"{CWD}/src/app.py"),
    ]
    first, second = redactor.redact_session(records)

    pseudonym = first["cwd"]
    assert pseudonym.startswith("/repo-") and "leigh" not in pseudonym
    assert first["message"]["content"][0]["text"] == f"look at {pseudonym}/src/app.py"
    assert second["message"]["content"][0]["input"]["file_path"] == (
        f"{pseudonym}/src/app.py"
    )
    assert "leigh" not in text_of(first) + text_of(second)


@pytest.mark.req("TER-SRC-021")
def test_pseudonyms_are_stable_for_one_salt_and_differ_across_salts() -> None:
    def pseudonym(salt: str) -> str:
        return str(Redactor(RedactionPolicy(salt=salt)).redact(user("x"))["cwd"])

    assert pseudonym("a") == pseudonym("a")
    assert pseudonym("a") != pseudonym("b")


@pytest.mark.req("TER-SRC-021")
def test_home_directories_and_encoded_project_folders_are_pseudonymised() -> None:
    redactor = Redactor()
    out = redactor.redact(
        user(
            "see /home/leigh/.ssh/config, /Users/sam/notes and "
            "~/.claude/projects/-home-leigh-work-acme-billing/x.jsonl"
        )
    )
    text = out["message"]["content"][0]["text"]
    assert "leigh" not in text and "sam" not in text
    assert "/home-" in text and "-repo-" in text


@pytest.mark.req("TER-SRC-021")
def test_a_working_directory_seen_late_is_replaced_in_earlier_records() -> None:
    early = user("earlier mention of /srv/build/acme", cwd=None)
    late = user("now", cwd="/srv/build/acme")
    out = Redactor().redact_session([early, late])
    assert "/srv/build/acme" not in text_of(out[0])


def test_file_contents_the_agent_read_are_replaced_by_their_size_and_hash() -> None:
    redactor = Redactor()
    body = "line one\nline two\nline three"
    out = redactor.redact_session(
        [tool_use("c1", "Read", file_path="/x"), tool_result("c1", body)]
    )[1]
    content = out["message"]["content"][0]["content"]
    assert content.startswith("[file content: 3 lines, sha256:")
    assert "line two" not in text_of(out)
    assert redactor.counts()["file-content"] == 1


def test_file_contents_are_kept_but_scrubbed_when_the_corpus_may_quote_them() -> None:
    policy = RedactionPolicy(quote_file_contents=True)
    out = Redactor(policy).redact_session(
        [
            tool_use("c1", "Read", file_path="/x"),
            tool_result("c1", [{"type": "text", "text": "key AKIAABCDEFGHIJKLMNOP"}]),
        ]
    )[1]
    block = out["message"]["content"][0]["content"][0]
    assert block["text"] == "key [REDACTED:aws-access-key]"


def test_long_tool_output_is_dropped_unless_its_tool_is_kept() -> None:
    long = "x" * 2001
    records = [tool_use("c1", "Bash", command="ls"), tool_result("c1", long)]

    dropped = Redactor().redact_session(records)[1]["message"]["content"][0]
    assert dropped["content"].startswith("[tool output dropped: 2001 chars, sha256:")

    kept_policy = RedactionPolicy(keep_tools=frozenset({"Bash"}))
    kept = Redactor(kept_policy).redact_session(records)[1]["message"]["content"][0]
    assert kept["content"] == long


def test_output_at_the_limit_is_kept() -> None:
    records = [tool_use("c1", "Bash", command="ls"), tool_result("c1", "x" * 2000)]
    block = Redactor().redact_session(records)[1]["message"]["content"][0]
    assert block["content"] == "x" * 2000


def test_tool_use_result_copies_and_image_data_are_dropped() -> None:
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0K"},
    }
    record = tool_result("c1", "ok")
    record["message"]["content"].append(image)
    redactor = Redactor()
    out = redactor.redact(record)

    assert out["toolUseResult"] == "[redacted]"
    assert out["message"]["content"][1]["source"]["data"].startswith("[image dropped")
    assert out["message"]["content"][1]["source"]["media_type"] == "image/png"
    assert {"tool-use-result", "image"} <= set(redactor.counts())


def test_identifiers_usage_and_structure_are_never_changed() -> None:
    record = tool_use("call_ghp_" + "a1" * 12, "Bash", command="echo hi")
    out = Redactor().redact(record)
    message, original = out["message"], record["message"]
    for key in ("id", "role", "model", "usage"):
        assert message[key] == original[key]
    block = message["content"][0]
    assert block["id"] == original["content"][0]["id"]  # ids are not secrets
    assert block["name"] == "Bash"
    assert set(block["input"]) == {"command"}
    assert out["uuid"] == record["uuid"] and out["sessionId"] == "s1"


def test_redaction_does_not_change_the_input_records() -> None:
    record = user(f"{CWD} leigh@example.org")
    before = json.dumps(record, sort_keys=True)
    Redactor().redact(record)
    assert json.dumps(record, sort_keys=True) == before


@pytest.mark.req("TER-SRC-022")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a_redacted_golden_session_yields_the_same_events(
    name: str, tmp_path: Path
) -> None:
    path = CORPUS[name]
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    redacted = tmp_path / "redacted.jsonl"
    redacted.write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in Redactor(RedactionPolicy(max_tool_output=10)).redact_session(
                records
            )
        ),
        encoding="utf-8",
    )
    source = ClaudeCodeJsonlSource()
    before, after = source.read(path), source.read(redacted)

    def shape(trace: Any) -> list[tuple[object, ...]]:
        return [
            (
                e.id,
                e.kind,
                e.actor,
                e.tool.kind if e.tool else None,
                e.tool.call_id if e.tool else None,
            )
            for e in trace.events
        ]

    assert shape(after) == shape(before)
    assert after.coverage == before.coverage
