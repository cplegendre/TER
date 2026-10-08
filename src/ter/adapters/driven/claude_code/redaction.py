"""Redaction of Claude Code JSONL records before anything is analysed or shared.

Issue #34: real sessions hold secrets, personal paths and other people's code.
A :class:`Redactor` rewrites one session's records so that:

- strings matching a secret pattern (cloud keys, API tokens, private keys,
  bearer tokens, emails, IPv4 addresses, ``password=...`` assignments) become
  ``[REDACTED:<kind>#<tag>]``, where the tag is a short salted hash, so two
  different secrets stay different and one secret stays the same;
- each working directory and user home becomes a pseudonym that is the same
  everywhere in a corpus (``/repo-<hash>``, ``/home-<hash>``), so a file read
  twice is still the same path after redaction;
- the content of files the agent read becomes ``[file content: N lines,
  sha256:...]`` unless the corpus may quote it;
- any other tool output longer than a limit is dropped to a placeholder with
  its length and hash, unless its tool is on a keep list;
- image data and Claude Code's ``toolUseResult`` copies are dropped.

Record types, uuids, parent links, timestamps, tool names, tool argument keys
and usage blocks are never changed, so a redacted session yields the same
``ter.event`` ids as the original (TER-SRC-022). Every replacement is logged as
a :class:`Redaction` (kind, record line, JSON path, length) without the value,
so a reviewer can check what was removed.

Pure: no IO. The corpus importer reads and writes files around it.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SECRET_PATTERNS",
    "Redaction",
    "RedactionPolicy",
    "Redactor",
]

#: (kind, pattern). Order matters: longer, more specific patterns first.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|\Z)", re.DOTALL
        ),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,})"),
    ),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"(?P<sep>[\"']?\s*[:=]\s*[\"']?)(?!\[REDACTED:)[^\s\"',;]{6,}"
        ),
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "ipv4",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
        ),
    ),
)

#: A user's home directory at the start of an absolute path.
_HOME = re.compile(
    r"(?:/home/[^/\s\"'\\]+|/Users/[^/\s\"'\\]+|/root(?=/|\b)|[A-Za-z]:\\\\?Users\\\\?[^\\/\s\"']+)"
)
#: Tools whose results are file contents.
_FILE_READERS = frozenset({"Read", "NotebookRead"})


@dataclass(frozen=True)
class RedactionPolicy:
    """What a corpus may keep. The defaults keep the least."""

    #: Tool outputs longer than this many characters are dropped.
    max_tool_output: int = 2000
    #: Tools whose outputs are kept whatever their length (still scrubbed).
    keep_tools: frozenset[str] = frozenset()
    #: Keep the content of files the agent read (still scrubbed). Only for
    #: repositories whose licence allows redistribution.
    quote_file_contents: bool = False
    #: Mixed into every pseudonym, so they cannot be reversed by guessing
    #: common paths. Keep it private and the same across one corpus.
    salt: str = ""


@dataclass(frozen=True)
class Redaction:
    """One replacement, located but without the value it removed."""

    kind: str
    line: int
    path: str
    length: int


@dataclass
class _Roots:
    """Path prefixes and their pseudonyms, longest first."""

    pairs: list[tuple[str, str]] = field(default_factory=list)

    def add(self, prefix: str, pseudonym: str) -> None:
        if prefix and all(p != prefix for p, _ in self.pairs):
            self.pairs.append((prefix, pseudonym))
            self.pairs.sort(key=lambda pair: len(pair[0]), reverse=True)


class Redactor:
    """Redacts the records of one session, in order.

    Call :meth:`learn` with every record first (or let :meth:`redact_session`
    do it), so that working directories seen late in a session are replaced
    everywhere.
    """

    def __init__(self, policy: RedactionPolicy | None = None) -> None:
        self.policy = policy or RedactionPolicy()
        self._roots = _Roots()
        self._tools: dict[str, str] = {}
        self.redactions: list[Redaction] = []

    # -- whole sessions -------------------------------------------------

    def redact_session(
        self, records: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Redact every record of a session, learning its paths first."""
        materialised = list(records)
        for record in materialised:
            self.learn(record)
        return [
            self.redact(record, line) for line, record in enumerate(materialised, 1)
        ]

    def counts(self) -> dict[str, int]:
        """Replacements by kind, sorted by kind."""
        return dict(sorted(Counter(r.kind for r in self.redactions).items()))

    # -- one record -----------------------------------------------------

    def learn(self, record: Mapping[str, Any]) -> None:
        """Register the working directory and home directories a record names."""
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd.strip("/\\"):
            root = cwd.rstrip("/\\")
            pseudonym = "/repo-" + self._hash(root)
            self._roots.add(root, pseudonym)
            # Claude Code names project folders after the path, "/" -> "-".
            self._roots.add(re.sub(r"[/\\:.]", "-", root), "-repo-" + self._hash(root))
        for text in _strings(record):
            for match in _HOME.finditer(text):
                home = match.group(0)
                self._roots.add(home, "/home-" + self._hash(home))

    def scrub(self, text: str, line: int = 0, path: str = "") -> str:
        """Apply the secret patterns and path pseudonyms to free text."""
        return self._text(text, line, path)

    def redact(self, record: Mapping[str, Any], line: int = 0) -> dict[str, Any]:
        """Return a redacted copy of one record."""
        self.learn(record)
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key == "toolUseResult" and value is not None:
                self._log("tool-use-result", line, key, value)
                out[key] = "[redacted]"
            elif key == "message" and isinstance(value, Mapping):
                out[key] = self._message(value, line)
            else:
                out[key] = self._value(value, line, key)
        return out

    def _message(self, message: Mapping[str, Any], line: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in message.items():
            path = f"message.{key}"
            if key == "content" and isinstance(value, list):
                out[key] = [
                    self._block(block, line, f"{path}[{index}]")
                    for index, block in enumerate(value)
                ]
            elif key in ("usage", "id", "model", "role", "stop_reason"):
                out[key] = value
            else:
                out[key] = self._value(value, line, path)
        return out

    def _block(self, block: object, line: int, path: str) -> object:
        if not isinstance(block, Mapping):
            return self._value(block, line, path)
        kind = block.get("type")
        if kind == "tool_use":
            name, call = block.get("name"), block.get("id")
            if isinstance(name, str) and isinstance(call, str):
                self._tools[call] = name
            return {
                k: (
                    v
                    if k in ("type", "id", "name")
                    else self._value(v, line, f"{path}.{k}")
                )
                for k, v in block.items()
            }
        if kind == "tool_result":
            return self._tool_result(block, line, path)
        if kind == "image":
            return self._image(block, line, path)
        if kind == "thinking":
            # The signature is an opaque provider token: kept as is.
            return {
                k: (
                    v
                    if k in ("type", "signature")
                    else self._value(v, line, f"{path}.{k}")
                )
                for k, v in block.items()
            }
        return {
            k: (v if k == "type" else self._value(v, line, f"{path}.{k}"))
            for k, v in block.items()
        }

    def _tool_result(
        self, block: Mapping[str, Any], line: int, path: str
    ) -> dict[str, Any]:
        tool = self._tools.get(str(block.get("tool_use_id")), "")
        content = block.get("content")
        text = _result_text(content)
        out = dict(block)
        if (
            text is not None
            and tool in _FILE_READERS
            and not self.policy.quote_file_contents
        ):
            self._log("file-content", line, f"{path}.content", text)
            out["content"] = (
                f"[file content: {text.count(chr(10)) + 1} lines, sha256:{_sha(text)}]"
            )
        elif (
            text is not None
            and len(text) > self.policy.max_tool_output
            and tool not in self.policy.keep_tools
            and tool not in _FILE_READERS  # quoted file contents are only scrubbed
        ):
            self._log("tool-output", line, f"{path}.content", text)
            out["content"] = (
                f"[tool output dropped: {len(text)} chars, sha256:{_sha(text)}]"
            )
        elif isinstance(content, list):
            # Tools such as Read return images as content blocks too.
            out["content"] = [
                self._image(item, line, f"{path}.content[{i}]")
                if isinstance(item, Mapping) and item.get("type") == "image"
                else self._value(item, line, f"{path}.content[{i}]")
                for i, item in enumerate(content)
            ]
        else:
            out["content"] = self._value(content, line, f"{path}.content")
        return {
            k: (
                v
                if k in ("type", "tool_use_id", "is_error", "content")
                else self._value(v, line, f"{path}.{k}")
            )
            for k, v in out.items()
        }

    def _image(self, block: Mapping[str, Any], line: int, path: str) -> dict[str, Any]:
        out = dict(block)
        source = block.get("source")
        if isinstance(source, Mapping) and "data" in source:
            data = str(source.get("data"))
            self._log("image", line, f"{path}.source.data", data)
            out["source"] = {**source, "data": f"[image dropped: sha256:{_sha(data)}]"}
        return out

    # -- values ---------------------------------------------------------

    def _value(self, value: object, line: int, path: str) -> Any:
        if isinstance(value, str):
            return self._text(value, line, path)
        if isinstance(value, Mapping):
            out: dict[Any, Any] = {}
            for key, item in value.items():
                # Keys are scrubbed too: tool arguments can have any keys.
                safe = self._text(key, line, path) if isinstance(key, str) else key
                out[safe] = self._value(item, line, f"{path}.{safe}")
            return out
        if isinstance(value, list):
            return [self._value(v, line, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    def _text(self, text: str, line: int, path: str) -> str:
        for kind, pattern in SECRET_PATTERNS:
            text = pattern.sub(self._replacer(kind, line, path), text)
        for prefix, pseudonym in self._roots.pairs:
            if prefix in text:
                self.redactions.append(
                    Redaction("path", line, path, text.count(prefix) * len(prefix))
                )
                text = text.replace(prefix, pseudonym)
        return text

    def _replacer(
        self, kind: str, line: int, path: str
    ) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            return self._secret(kind, match, line, path)

        return replace

    def _secret(self, kind: str, match: re.Match[str], line: int, path: str) -> str:
        found = match.group(0)
        self.redactions.append(Redaction(kind, line, path, len(found)))
        head = ""
        if kind == "assigned-secret":
            # Keep the name and separator so the code still reads.
            end = match.end("sep") - match.start()
            head, found = found[:end], found[end:]
        # A salted tag keeps different secrets different, so calls or blocks
        # that differ only in a secret stay distinct after redaction.
        return f"{head}[REDACTED:{kind}#{self._hash(found)[:8]}]"

    def _log(self, kind: str, line: int, path: str, value: object) -> None:
        self.redactions.append(Redaction(kind, line, path, len(str(value))))

    def _hash(self, value: str) -> str:
        return _sha(self.policy.salt + "\0" + value)


def _result_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(c.get("text", ""))
            for c in content
            if isinstance(c, Mapping) and c.get("type") == "text"
        ]
        return "\n".join(parts) if parts else None
    return None


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
