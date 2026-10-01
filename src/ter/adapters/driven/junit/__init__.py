"""An :class:`~ter.ports.driven.OutcomeSource` that reads JUnit XML test results.

JUnit XML is what ``pytest --junitxml``, Maven Surefire, Gradle, Jest
(``jest-junit``), Go (``go-junit-report``) and most CI runners write, so the
reference adapter for outcomes is tied to no agent or harness. A run
reference is the path of one results file.

Each ``<testcase>`` becomes one :class:`~ter.domain.outcome.CheckEvidence`,
identified as ``<classname>::<name>`` (or ``<name>`` when there is no class
name): a ``<failure>`` child is *failed*, ``<error>`` is *error*,
``<skipped>`` is *skipped*, and no such child is *passed*.

The file comes from outside TER, and ``defusedxml`` is not a dependency, so a
document that declares a DOCTYPE (where entity definitions live) is rejected
before it is parsed: no entity is ever expanded and nothing external is
fetched. Test-result files never need one.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NoReturn
from xml.parsers import expat

from ....domain.outcome import (
    CheckEvidence,
    CheckStatus,
    OutcomeEvidence,
    OutcomeFormatError,
)

__all__ = ["JUnitOutcomeSource"]

#: Longest failure message kept per check; the full text stays in the file.
DETAIL_LIMIT = 300

_STATUS_TAGS = (
    ("error", CheckStatus.ERROR),
    ("failure", CheckStatus.FAILED),
    ("skipped", CheckStatus.SKIPPED),
)


class _DoctypeDeclared(Exception):
    pass


def _refuse(*_: object) -> NoReturn:
    raise _DoctypeDeclared


def _reject_doctype(data: bytes, path: Path) -> None:
    """Fail if the document declares a DOCTYPE or any entity, before expansion."""
    scanner = expat.ParserCreate()
    scanner.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    scanner.StartDoctypeDeclHandler = _refuse
    scanner.EntityDeclHandler = _refuse
    try:
        scanner.Parse(data, True)
    except _DoctypeDeclared:
        raise OutcomeFormatError(
            f"{path}: test results must not declare a DOCTYPE or entities"
        ) from None
    except expat.ExpatError as exc:
        raise OutcomeFormatError(f"{path}: not well-formed XML: {exc}") from None


def _detail(element: ET.Element) -> str:
    text = element.get("message") or (element.text or "").strip()
    first = text.splitlines()[0] if text else ""
    return first if len(first) <= DETAIL_LIMIT else first[: DETAIL_LIMIT - 1] + "…"


def _seconds(raw: str | None) -> float | None:
    """A test duration, or None when it is absent, unreadable or not finite.

    With a ``.`` present a comma is a thousands separator (``1,234.5``);
    alone it is a decimal comma (``0,010`` is 0.01 seconds).
    """
    if raw is None:
        return None
    text = raw.strip()
    text = text.replace(",", "") if "." in text else text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


class JUnitOutcomeSource:
    """Reads one JUnit XML file per run reference."""

    name = "junit"

    def outcome(self, ref: str | Path) -> OutcomeEvidence | None:
        path = Path(ref)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except (IsADirectoryError, PermissionError) as exc:
            raise OutcomeFormatError(
                f"{path}: cannot read test results: {exc}"
            ) from exc
        _reject_doctype(data, path)
        root = ET.fromstring(data)  # well-formed and DOCTYPE-free, checked above
        if root.tag not in ("testsuites", "testsuite"):
            raise OutcomeFormatError(
                f"{path}: root element <{root.tag}> is not <testsuites> or <testsuite>"
            )
        checks: list[CheckEvidence] = []
        for index, case in enumerate(root.iter("testcase"), start=1):
            name = (case.get("name") or "").strip()
            if not name:
                raise OutcomeFormatError(f"{path}: testcase {index} has no name")
            classname = (case.get("classname") or "").strip()
            status, detail = CheckStatus.PASSED, ""
            for tag, mapped in _STATUS_TAGS:
                found = case.find(tag)
                if found is not None:
                    status, detail = mapped, _detail(found)
                    break
            checks.append(
                CheckEvidence(
                    check_id=f"{classname}::{name}" if classname else name,
                    status=status,
                    source=f"{path.name}#testcase-{index}",
                    detail=detail,
                    seconds=_seconds(case.get("time")),
                )
            )
        return OutcomeEvidence(
            run_ref=str(path), source=self.name, checks=tuple(checks)
        )
