"""TER's software bill of materials (TER-SBM-001, TER-SBM-002).

``scripts/sbom.py`` installs the wheel in a fresh environment and writes a
CycloneDX SBOM of everything that install holds. These tests read that SBOM:
CI's package job points ``TER_SBOM`` at the one it built from its wheel and
uploads; without it, one is built from this checkout (that needs network
access to install the dependencies and the SBOM tool).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]
#: Distributions whose packaging metadata declares no licence, so the SBOM
#: cannot name one. Only shrinks: a new dependency without a licence fails
#: here, and is reviewed before it ships.
UNDECLARED_LICENCE = {"tiktoken"}
#: Build and test tooling that must never be listed as something TER installs.
TOOLING = {"pip", "setuptools", "wheel", "pytest", "cyclonedx-bom", "build"}


@pytest.fixture(scope="module")
def sbom(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    given = os.environ.get("TER_SBOM")
    if given:
        path = Path(given)
    else:
        path = tmp_path_factory.mktemp("sbom") / "ter.cdx.json"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sbom.py"), "--out", str(path)],
            check=True,
            capture_output=True,
            timeout=600,
        )
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _components(sbom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {canonicalize_name(c["name"]): c for c in sbom["components"]}


@pytest.mark.req("TER-SBM-001")
def test_the_sbom_describes_this_release_of_ter(sbom: dict[str, Any]) -> None:
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.6"
    root = sbom["metadata"]["component"]
    assert (root["name"], root["version"]) == (PROJECT["name"], PROJECT["version"])
    assert root["licenses"] == [
        {"license": {"acknowledgement": "declared", "id": PROJECT["license"]}}
    ]


@pytest.mark.req("TER-SBM-001")
def test_every_runtime_dependency_is_listed_at_a_version_in_its_range(
    sbom: dict[str, Any],
) -> None:
    components = _components(sbom)
    refs: dict[str, str] = {}
    for requirement in map(Requirement, PROJECT["dependencies"]):
        name = canonicalize_name(requirement.name)
        assert name in components, f"{name} is installed but not in the SBOM"
        version = components[name]["version"]
        assert requirement.specifier.contains(version), f"{name} {version}"
        refs[components[name]["bom-ref"]] = name
    (root,) = [d for d in sbom["dependencies"] if d["ref"] == "root-component"]
    assert set(root["dependsOn"]) == set(refs)


@pytest.mark.req("TER-SBM-001")
def test_every_component_has_an_exact_version_a_purl_and_a_licence(
    sbom: dict[str, Any],
) -> None:
    for name, component in _components(sbom).items():
        version = component["version"]
        assert component["purl"].lower() == f"pkg:pypi/{name}@{version}".lower()
        if name in UNDECLARED_LICENCE:
            assert "licenses" not in component, f"{name} now declares a licence"
        else:
            assert component.get("licenses"), f"{name} declares no licence"


@pytest.mark.req("TER-SBM-001")
def test_every_listed_component_is_reachable_from_ter(sbom: dict[str, Any]) -> None:
    """Only what installing TER installs: no tooling, nothing unreachable."""
    graph = {d["ref"]: d.get("dependsOn", []) for d in sbom["dependencies"]}
    reached: set[str] = set()
    todo = ["root-component"]
    while todo:
        ref = todo.pop()
        if ref not in reached:
            reached.add(ref)
            todo.extend(graph.get(ref, []))
    listed = {c["bom-ref"] for c in sbom["components"]}
    assert listed <= reached, sorted(listed - reached)
    assert not TOOLING & set(_components(sbom))


def _package_job() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["package"]["steps"]
    return steps


@pytest.mark.req("TER-SBM-002")
def test_ci_builds_the_sbom_from_its_wheel_checks_it_and_uploads_it() -> None:
    steps = _package_job()
    names = [s.get("name") for s in steps]
    build = steps[names.index("Write the SBOM of the built wheel")]
    check = steps[names.index("Check the SBOM")]
    upload = steps[names.index("Upload the SBOM")]
    assert names.index("Build distributions") < names.index(build["name"])
    assert "scripts/sbom.py --wheel dist/" in build["run"]
    assert check["env"]["TER_SBOM"] == "dist/ter.cdx.json"
    assert "tests/sbom" in check["run"]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"] == {"name": "sbom", "path": "dist/ter.cdx.json"}


@pytest.mark.req("TER-SBM-001")
def test_the_sbom_tool_stays_out_of_the_test_environment() -> None:
    """cyclonedx-bom's validation installs jsonschema's format checkers, which
    would change what the schema contract tests accept (tests/schema)."""
    extras = PROJECT["optional-dependencies"]
    named = {canonicalize_name(Requirement(r).name) for e in extras.values() for r in e}
    assert not {n for n in named if n.startswith("cyclonedx")}
