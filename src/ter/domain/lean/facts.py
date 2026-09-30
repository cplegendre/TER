"""Facts read from single events: what a shell command is for, whether a
validation run passed, which files a call touches, which words a text uses.

Every function here is pure, deterministic and conservative: when a fact
cannot be read reliably it is reported as unknown (``Outcome.UNKNOWN``,
``ShellIntent.OTHER``) rather than guessed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from .model import Outcome, ShellIntent

__all__ = [
    "content_words",
    "defined_identifiers",
    "failure_signature",
    "fingerprint",
    "output_fingerprint",
    "normalise_command",
    "overlap",
    "shell_intent",
    "source_lines",
    "tool_paths",
    "validation_outcome",
]

_PATH_KEYS = ("file_path", "notebook_path", "path")

# Checked in this order: a change anywhere in a compound command makes it a
# change; otherwise a validation anywhere makes it a validation.
_CHANGE = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:"
    r"(?:pip3?|uv pip|poetry|npm|yarn|pnpm|cargo|go|gem|bundle|apt(?:-get)?|brew)"
    r"\s+(?:install|add|remove|uninstall|get|update|upgrade)"
    r"|git\s+(?:add|commit|checkout|switch|reset|restore|apply|merge|rebase|mv|rm|push|stash|cherry-pick)"
    r"|mkdir|rm|mv|cp|touch|chmod|chown|ln|patch|sed\s+-i|tee"
    r")\b"
)
_VALIDATE = re.compile(
    r"(?:^|[;&|(]\s*|\s|/)(?:"
    r"pytest|py\.test|tox|nox|unittest|doctest|jest|vitest|mocha|ava|karma|rspec"
    r"|phpunit|ctest|ruff|mypy|pyright|flake8|pylint|bandit|eslint|tsc|prettier\s+--check"
    r"|black\s+--check|isort\s+--check|shellcheck|hadolint|golangci-lint|lint-imports"
    r"|go\s+(?:test|vet|build)|cargo\s+(?:test|check|clippy|build)|mvn|gradlew?\s+\w*(?:test|check|build)"
    r"|dotnet\s+(?:test|build)|make\s+(?:test|check|lint|ci)|(?:npm|yarn|pnpm)\s+(?:run\s+)?(?:test|lint|check|build|typecheck)"
    r"|playwright\s+test|cypress\s+run|bazel\s+test|swift\s+test"
    r")\b"
)
# Running code to check it by hand: ``python -c``, ``python - <<EOF``,
# ``python -m pkg``, ``node script.js``. Counted as validation only when the
# command is not also a change.
_ADHOC_RUN = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:python3?|node|deno|bun|ruby|php)\s+(?:-c\b|-\s|-m\s+\w|[\w./-]+\.(?:py|js|mjs|ts|rb|php)\b)"
)
_EXPLORE = re.compile(
    r"^\s*(?:cd\s+\S+\s*&&\s*)?(?:ls|cat|head|tail|less|more|find|grep|rg|ag|tree|pwd|wc|file|stat|du"
    r"|which|type|echo|env|printenv|git\s+(?:status|log|diff|show|blame|branch|remote|ls-files|grep))\b"
)

# Output markers. Failure markers are specific on purpose (precision over
# recall): a bare "error" in prose output is not a failure.
_FAILED = re.compile(
    r"(?:\b\d+\s+(?:failed|failures?|errors?)\b(?<!\b0 failed)(?<!\b0 errors)(?<!\b0 error)"
    r"|^FAILED\b|^FAIL\b|\bFAILED\s+\S+::|^ERROR[:\s]|^E\s{2,}\S"
    r"|Traceback \(most recent call last\)|^\w*Error: |^error(?:\[\w+\])?: "
    r"|\bexit (?:code|status) [1-9]\d*|\bExit code [1-9]\d*|command not found"
    r"|npm ERR!|^\s*[✗✘×] |\bFound \d+ errors?\b(?<!Found 0 errors)|\bTests?:\s+\d+ failed)",
    re.MULTILINE,
)
_PASSED = re.compile(
    r"(?:\b\d+\s+passed\b|^OK\b|\bAll checks passed\b|\bSuccess(?:fully)?\b|\bchecks? passed\b"
    r"|\bno issues found\b|\b0 errors?\b|\bTests?:\s+\d+ passed\b|^ok\s|\bBUILD SUCCESSFUL\b"
    r"|\bFinished\b.*\btarget\(s\)|^PASS\b)",
    re.MULTILINE | re.IGNORECASE,
)
_NOISE = re.compile(
    r"(?:\bin\s+\d+(?:\.\d+)?\s*m?s\b|\d+(?:\.\d+)?s\b|0x[0-9a-f]+|\b\d{4}-\d\d-\d\d[T ][\d:.]+Z?)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[.-][A-Za-z0-9_]+)*")
_DEFINED = re.compile(
    r"(?:\b(?:def|class|function|func|fn|const|let|var|struct|interface|type|enum|trait|impl)\s+"
    r"([A-Za-z_][A-Za-z0-9_]{2,})|^([A-Z][A-Z0-9_]{2,})\s*[:=])",
    re.MULTILINE,
)

_STOPWORDS = frozenset(
    """
    a an the and or but if then else so to of in on at by for with from into onto
    is are was were be been being it its this that these those there here i we you
    he she they them our my your me us do does did done can could should would will
    shall may might must need needs let lets now just also still again only very
    what which who whom whose when where why how all any each some more most other
    such no not nor too than as up out about over under before after while
    make sure first next last think file files use using used
    """.split()
)


def normalise_command(command: str) -> str:
    """Collapse whitespace, so equal commands compare equal."""
    return " ".join(command.split())


def shell_intent(command: str) -> ShellIntent:
    """Classify a shell command line by what it is for."""
    text = normalise_command(command)
    if not text:
        return ShellIntent.OTHER
    if _CHANGE.search(text):
        return ShellIntent.CHANGE
    if _VALIDATE.search(text) or _ADHOC_RUN.search(text):
        return ShellIntent.VALIDATE
    if _EXPLORE.search(text):
        return ShellIntent.EXPLORE
    return ShellIntent.OTHER


def validation_outcome(output: str) -> Outcome:
    """Read pass or fail from a validation run's output; unknown when unclear."""
    if _FAILED.search(output):
        return Outcome.FAILED
    if _PASSED.search(output):
        return Outcome.PASSED
    return Outcome.UNKNOWN


def failure_signature(output: str) -> str:
    """A hash of a failing run's failure lines, with timings and addresses removed.

    Two runs with equal signatures failed the same way.
    """
    lines = sorted(
        {
            _NOISE.sub("#", line.strip())
            for line in output.splitlines()
            if _FAILED.search(line.strip())
        }
    )
    basis = "\n".join(lines) if lines else _NOISE.sub("#", output.strip())
    return fingerprint(basis)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def output_fingerprint(text: str) -> str:
    """Fingerprint of tool output with timings, addresses and timestamps removed,
    so two results that differ only in how long they took compare equal."""
    return fingerprint(_NOISE.sub("#", text.strip()))


def tool_paths(arguments: Mapping[str, object]) -> tuple[str, ...]:
    """File paths a file tool call names (empty for searches and shell)."""
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return (value,)
    return ()


def content_words(text: str) -> frozenset[str]:
    """Lower-cased content words of a text, without stopwords or short words."""
    return frozenset(
        w
        for w in (m.group(0).lower() for m in _WORD.finditer(text))
        if len(w) > 2 and w not in _STOPWORDS
    )


def defined_identifiers(text: str) -> frozenset[str]:
    """Names a source text defines (functions, classes, constants)."""
    return frozenset(a or b for a, b in _DEFINED.findall(text))


_LINE_NUMBER = re.compile(r"^\s*\d+(?:→|\t)")


def source_lines(text: str) -> frozenset[str]:
    """Distinct non-blank lines of a file's text, stripped, without the line
    numbers a read tool may prefix."""
    return frozenset(
        stripped
        for stripped in (
            _LINE_NUMBER.sub("", line).strip() for line in text.splitlines()
        )
        if stripped
    )


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap coefficient ``|a ∩ b| / min(|a|, |b|)``; 0 when either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
