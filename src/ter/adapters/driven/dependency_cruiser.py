"""Architecture contracts in dependency-cruiser's configuration format.

Reads the ``forbidden`` rules a TypeScript or JavaScript repository declares
for `dependency-cruiser <https://github.com/sverweij/dependency-cruiser>`_,
from ``.dependency-cruiser.json`` (TER-EVD-015). Each rule becomes one
``path_forbidden`` contract: no file whose repository path matches the
rule's ``from`` side imports a file whose path matches its ``to`` side.

What is read, and what is left out (never guessed):

* a rule's ``from`` and ``to`` conditions ``path`` and ``pathNot`` (a
  regular expression or a list of them; a list matches when any item
  does), including group matching (``$1`` in ``to.path`` stands for the
  first group ``from.path`` matched). A side with no condition matches
  every file;
* a rule with any other condition (``circular``, ``orphan``,
  ``dependencyTypes``, ``reachable``, ``couldNotResolve``, ``via``,
  ``dynamic``, ``license``, ...) is skipped: TER sees only imports that
  load repository files, so it cannot judge those conditions;
* a rule with ``severity: "off"`` is skipped; ``allowed`` rules (anything
  not allowed is forbidden) are not read;
* ``.dependency-cruiser.js``, ``.cjs`` and ``.mjs`` configurations are
  programs, not data, and are not read; a repository that keeps its rules
  there declares nothing TER can see.

Regular expressions are compiled as Python's :mod:`re` reads them; the
JavaScript syntax rules use (anchors, classes, groups, alternation) reads
the same. One that does not compile makes the file unreadable
(``ContractFormatError``). The adapter does no IO: it parses text the
caller read through ``RepositoryEvidence`` (TER-EVD-001).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from ...domain.repository import (
    ArchitectureContract,
    ContractFormatError,
    ContractKind,
)

__all__ = ["DependencyCruiserContracts"]

#: The only conditions TER evaluates on a rule's ``from`` and ``to`` sides.
_PATH_KEYS = frozenset({"path", "pathNot"})


def _patterns(value: object, where: str, key: str) -> tuple[str, ...]:
    """One regular expression or a list of them, each checked to compile."""
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise ContractFormatError(f"{where}: {key} must be a regular expression")
    for item in items:
        try:
            re.compile(re.sub(r"\$[1-9]", "", item))
        except re.error as exc:
            raise ContractFormatError(f"{where}: {key} {item!r}: {exc}") from exc
    return tuple(items)


def _side(rule: Mapping[str, object], key: str, where: str) -> Mapping[str, object]:
    side = rule.get(key, {})
    if not isinstance(side, dict):
        raise ContractFormatError(f"{where}: {key} must be an object")
    return side


def _rule(rule: object, n: int, where: str) -> ArchitectureContract | None:
    if not isinstance(rule, dict):
        raise ContractFormatError(f"{where}: forbidden rule {n} must be an object")
    raw_name = rule.get("name", f"rule-{n}")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ContractFormatError(f"{where}: forbidden rule {n} name must be text")
    name = raw_name.strip()
    here = f"{where} rule {name}"
    if rule.get("severity") == "off":
        return None
    source, target = _side(rule, "from", here), _side(rule, "to", here)
    if set(source) - _PATH_KEYS or set(target) - _PATH_KEYS:
        return None  # a condition TER cannot judge from file imports
    comment = rule.get("comment")
    return ArchitectureContract(
        id=name,
        name=comment.strip() if isinstance(comment, str) and comment.strip() else name,
        kind=ContractKind.PATH_FORBIDDEN,
        source=where,
        from_paths=_patterns(source.get("path"), here, "from.path"),
        from_paths_not=_patterns(source.get("pathNot"), here, "from.pathNot"),
        to_paths=_patterns(target.get("path"), here, "to.path"),
        to_paths_not=_patterns(target.get("pathNot"), here, "to.pathNot"),
    )


class DependencyCruiserContracts:
    """An :class:`~ter.ports.driven.ArchitectureContracts` for
    dependency-cruiser's JSON configuration."""

    name = "dependency-cruiser"

    def sources(self) -> tuple[str, ...]:
        return (".dependency-cruiser.json",)

    def read(self, path: str, text: str) -> tuple[ArchitectureContract, ...]:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ContractFormatError(f"{path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ContractFormatError(f"{path}: the configuration must be an object")
        rules = data.get("forbidden", [])
        if not isinstance(rules, list):
            raise ContractFormatError(f"{path}: forbidden must be a list of rules")
        found = (_rule(rule, n, path) for n, rule in enumerate(rules, start=1))
        return tuple(c for c in found if c is not None)
