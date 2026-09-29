"""Load the EARS requirement catalogue from YAML files.

A catalogue is a directory of ``*.yaml`` files. Each file holds a mapping with
a ``requirements`` list; ``vocabulary.yaml`` (optional) holds a ``terms``
mapping from a discouraged term to the preferred one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ter.domain.maturity import Maturity
from ter.domain.requirements import Requirement, RequirementError

VOCABULARY_FILE = "vocabulary.yaml"


class CatalogueError(ValueError):
    """The catalogue cannot be read: bad YAML, wrong shape or a bad entry."""


@dataclass(frozen=True)
class Catalogue:
    """Requirements loaded from disk, with the file each one came from."""

    requirements: tuple[Requirement, ...]
    sources: dict[str, Path] = field(default_factory=dict)
    vocabulary: dict[str, str] = field(default_factory=dict)

    def ids(self) -> set[str]:
        return {r.id for r in self.requirements}


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogueError(f"{path}: invalid YAML: {exc}") from exc


def load_file(path: Path) -> list[Requirement]:
    """Load the requirements declared in one YAML file."""
    document = _read_yaml(path)
    if not isinstance(document, dict) or not isinstance(
        document.get("requirements"), list
    ):
        raise CatalogueError(f"{path}: expected a mapping with a 'requirements' list")
    file_level: Maturity | None = None
    if "level" in document:
        try:
            file_level = Maturity.parse(document["level"])
        except ValueError as exc:
            raise CatalogueError(f"{path}: {exc}") from exc
    requirements = []
    for index, entry in enumerate(document["requirements"]):
        if not isinstance(entry, dict):
            raise CatalogueError(f"{path}: entry {index} is not a mapping")
        try:
            requirement = Requirement.from_mapping(entry)
        except RequirementError as exc:
            raise CatalogueError(f"{path}: {exc}") from exc
        if file_level is not None and requirement.level is not file_level:
            raise CatalogueError(
                f"{path}: {requirement.id} is {requirement.level.code} "
                f"but the file holds {file_level.code} requirements"
            )
        requirements.append(requirement)
    return requirements


def load_vocabulary(path: Path) -> dict[str, str]:
    """Load a controlled vocabulary file (``terms: {avoid: prefer}``)."""
    document = _read_yaml(path)
    terms = document.get("terms") if isinstance(document, dict) else None
    if not isinstance(terms, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in terms.items()
    ):
        raise CatalogueError(f"{path}: expected 'terms' mapping of strings")
    return dict(terms)


def load_catalogue(directory: Path) -> Catalogue:
    """Load every ``*.yaml`` file under ``directory`` (sorted, non-recursive)."""
    if not directory.is_dir():
        raise CatalogueError(f"{directory}: catalogue directory not found")
    requirements: list[Requirement] = []
    sources: dict[str, Path] = {}
    vocabulary: dict[str, str] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name == VOCABULARY_FILE:
            vocabulary = load_vocabulary(path)
            continue
        for requirement in load_file(path):
            requirements.append(requirement)
            sources.setdefault(requirement.id, path)
    return Catalogue(tuple(requirements), sources, vocabulary)
