"""Load the EARS requirement catalogue from YAML files.

A catalogue is a directory of ``*.yaml`` files. Each file holds a mapping with
a ``requirements`` list; ``vocabulary.yaml`` (optional) holds a ``terms``
mapping from a discouraged term to the preferred one; ``points.yaml``
(optional) holds the vision points. ``RepositoryChecks`` answers whether a
verification entry names a test or CI step that exists in the repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ter.domain.maturity import Maturity
from ter.domain.points import PointError, Verification, VerificationKind, VisionPoint
from ter.domain.requirements import Requirement, RequirementError

VOCABULARY_FILE = "vocabulary.yaml"
POINTS_FILE = "points.yaml"
DEFAULT_WORKFLOWS = Path(".github/workflows")


class CatalogueError(ValueError):
    """The catalogue cannot be read: bad YAML, wrong shape or a bad entry."""


@dataclass(frozen=True)
class Catalogue:
    """Requirements loaded from disk, with the file each one came from."""

    requirements: tuple[Requirement, ...]
    sources: dict[str, Path] = field(default_factory=dict)
    vocabulary: dict[str, str] = field(default_factory=dict)
    points: tuple[VisionPoint, ...] = ()

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
    points: list[VisionPoint] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == VOCABULARY_FILE:
            vocabulary = load_vocabulary(path)
            continue
        if path.name == POINTS_FILE:
            points = load_points(path)
            continue
        for requirement in load_file(path):
            requirements.append(requirement)
            sources.setdefault(requirement.id, path)
    return Catalogue(tuple(requirements), sources, vocabulary, tuple(points))


def load_points(path: Path) -> list[VisionPoint]:
    """Load the vision points file (a mapping with a ``points`` list)."""
    document = _read_yaml(path)
    if not isinstance(document, dict) or not isinstance(document.get("points"), list):
        raise CatalogueError(f"{path}: expected a mapping with a 'points' list")
    points = []
    for index, entry in enumerate(document["points"]):
        if not isinstance(entry, dict):
            raise CatalogueError(f"{path}: point {index} is not a mapping")
        try:
            points.append(VisionPoint.from_mapping(entry))
        except PointError as exc:
            raise CatalogueError(f"{path}: {exc}") from exc
    return points


class RepositoryChecks:
    """Decides whether a ``test:`` or ``ci:`` verification entry exists.

    A test entry exists when its file exists under ``root`` and every
    ``::`` part names a ``def`` or ``class`` in that file (parametrisation
    ids are ignored). A CI entry exists when a step with that exact name is
    declared in a workflow under ``root/.github/workflows``.
    """

    def __init__(self, root: Path, workflows: Path = DEFAULT_WORKFLOWS) -> None:
        self.root = root
        self.workflows = root / workflows
        self._ci_steps: set[str] | None = None

    def ci_steps(self) -> set[str]:
        if self._ci_steps is None:
            names: set[str] = set()
            for path in sorted(self.workflows.glob("*.y*ml")):
                document = _read_yaml(path)
                jobs = document.get("jobs", {}) if isinstance(document, dict) else {}
                for job in jobs.values() if isinstance(jobs, dict) else []:
                    steps = job.get("steps", []) if isinstance(job, dict) else []
                    names.update(
                        str(step["name"])
                        for step in steps
                        if isinstance(step, dict) and "name" in step
                    )
            self._ci_steps = names
        return self._ci_steps

    def __call__(self, entry: Verification) -> bool:
        if entry.kind is VerificationKind.CI:
            return entry.target in self.ci_steps()
        if entry.kind is not VerificationKind.TEST:
            return True
        file_part, *names = entry.target.split("::")
        path = self.root / file_part
        if not path.is_file():
            return False
        source = path.read_text(encoding="utf-8")
        for name in names:
            bare = name.split("[", 1)[0]
            if not re.search(
                rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(bare)}\b", source, re.M
            ):
                return False
        return True
