"""TER's software bill of materials (TER-SBM-001, TER-SBM-002).

``scripts/sbom.py`` writes a CycloneDX SBOM of an environment holding the
wheel and checks it against that environment with ``problems``. The tests
here hold ``problems`` to every way an SBOM can misdescribe an install, on
small documents built in the test, so they need no network. CI's package job
runs the script on the environment it smoke-tests and then the last test
below on the result (``TER_SBOM`` and ``TER_SBOM_ENV``).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ter_sbom", ROOT / "scripts/sbom.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


sbom_script = _load_script()
Installed = sbom_script.Installed

META = {
    "name": "ter-calculator",
    "version": "3.0.0",
    "license": "Apache-2.0",
    "dependencies": ["rich>=13,<16", "tiktoken>=0.7,<1"],
}
INSTALLED = [
    Installed("ter-calculator", "3.0.0", ("Apache-2.0",)),
    Installed("rich", "15.0.0", ("MIT", "License :: OSI Approved :: MIT License")),
    Installed("Pygments", "2.21.0", ("BSD-2-Clause",)),
    Installed("mdurl", "0.1.2", ("License :: OSI Approved :: MIT License",)),
    Installed("tiktoken", "0.14.0", ("MIT License\n\nCopyright (c) 2022 OpenAI",)),
]


def _component(name: str, version: str, licence: str | None) -> dict[str, Any]:
    component: dict[str, Any] = {
        "bom-ref": f"{name}=={version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{name.lower()}@{version}",
        "type": "library",
    }
    if licence:
        component["licenses"] = [{"license": {"id": licence}}]
    return component


GOOD: dict[str, Any] = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "metadata": {
        "component": {
            "bom-ref": "root-component",
            "name": "ter-calculator",
            "version": "3.0.0",
            "licenses": [
                {"license": {"acknowledgement": "declared", "id": "Apache-2.0"}}
            ],
        }
    },
    "components": [
        _component("rich", "15.0.0", "MIT"),
        _component("Pygments", "2.21.0", "BSD-2-Clause"),
        _component("mdurl", "0.1.2", "MIT"),
        _component("tiktoken", "0.14.0", None),
    ],
    "dependencies": [
        {"ref": "root-component", "dependsOn": ["rich==15.0.0", "tiktoken==0.14.0"]},
        {"ref": "rich==15.0.0", "dependsOn": ["Pygments==2.21.0", "mdurl==0.1.2"]},
        {"ref": "Pygments==2.21.0"},
        {"ref": "mdurl==0.1.2"},
        {"ref": "tiktoken==0.14.0"},
    ],
}


def _problems(
    sbom: dict[str, Any], installed: list[Any] = INSTALLED, meta: Any = META
) -> list[str]:
    return list(sbom_script.problems(sbom, installed, meta))


def _edited(edit: Any) -> dict[str, Any]:
    sbom = copy.deepcopy(GOOD)
    edit(sbom)
    return sbom


def _by_name(sbom: dict[str, Any], name: str) -> dict[str, Any]:
    (component,) = [c for c in sbom["components"] if c["name"] == name]
    return component


@pytest.mark.req("TER-SBM-001")
def test_an_sbom_that_matches_the_install_has_no_problems() -> None:
    assert _problems(GOOD) == []


@pytest.mark.req("TER-SBM-001")
@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (
            lambda s: s["components"].remove(_by_name(s, "mdurl")),
            "mdurl is installed but not in the SBOM",
        ),
        (
            lambda s: s["components"].append(_component("pip", "26.0", "MIT")),
            "pip is in the SBOM but not installed",
        ),
        (
            lambda s: _by_name(s, "rich").update(version="14.0.0"),
            "rich is 14.0.0 in the SBOM but 15.0.0 installed",
        ),
        (
            lambda s: _by_name(s, "rich").update(purl="pkg:npm/rich@15.0.0"),
            "rich has package URL pkg:npm/rich@15.0.0",
        ),
        (
            lambda s: _by_name(s, "rich").pop("licenses"),
            "rich has no licence in the SBOM",
        ),
        (
            lambda s: _by_name(s, "rich").update(
                licenses=[{"license": {"id": "GPL-3.0-only"}}]
            ),
            "rich licence GPL-3.0-only is not what it declares",
        ),
        (
            lambda s: _by_name(s, "tiktoken").update(
                licenses=[{"license": {"id": "MIT"}}]
            ),
            "tiktoken now has a licence: take it off LICENCE_NOT_IN_SBOM",
        ),
        (
            lambda s: s["metadata"]["component"].update(version="2.0.0"),
            "root component is ter-calculator 2.0.0",
        ),
        (
            lambda s: s["dependencies"][1]["dependsOn"].append("idna==3.20"),
            "dependency graph names idna==3.20, which is not a component",
        ),
        (
            lambda s: s["dependencies"][0]["dependsOn"].remove("tiktoken==0.14.0"),
            "TER's direct dependencies in the graph are not pyproject.toml's",
        ),
        (
            lambda s: s["dependencies"][1].update(dependsOn=["Pygments==2.21.0"]),
            "mdurl==0.1.2 is listed but nothing TER installs depends on it",
        ),
    ],
    ids=[
        "missing-transitive",
        "not-installed",
        "wrong-version",
        "wrong-purl",
        "no-licence",
        "wrong-licence",
        "licence-now-declared",
        "wrong-root",
        "dangling-ref",
        "direct-dependency-missing",
        "unreachable",
    ],
)
def test_each_way_an_sbom_misdescribes_the_install_is_named(
    edit: Any, problem: str
) -> None:
    assert problem in _problems(_edited(edit))


@pytest.mark.req("TER-SBM-001")
def test_a_dependency_outside_its_declared_range_is_named() -> None:
    meta = META | {"dependencies": ["rich>=13,<15", "tiktoken>=0.7,<1"]}
    assert "rich 15.0.0 is outside rich<15,>=13" in _problems(GOOD, meta=meta)


@pytest.mark.req("TER-SBM-001")
def test_tooling_in_the_install_is_named() -> None:
    sbom = _edited(lambda s: s["components"].append(_component("pip", "26.0", "MIT")))
    installed = [*INSTALLED, Installed("pip", "26.0", ("MIT",))]
    assert "pip is tooling, which TER does not install" in _problems(sbom, installed)


@pytest.mark.req("TER-SBM-001")
def test_the_licence_list_names_only_runtime_dependencies() -> None:
    """LICENCE_NOT_IN_SBOM only shrinks, so a stale entry is removed."""
    meta = sbom_script.project()
    runtime = {canonicalize_name(Requirement(r).name) for r in meta["dependencies"]}
    assert set(sbom_script.LICENCE_NOT_IN_SBOM) <= runtime


@pytest.mark.req("TER-SBM-001")
def test_the_sbom_tool_stays_out_of_the_test_environment() -> None:
    """cyclonedx-bom's validation installs jsonschema's format checkers, which
    would change what the schema contract tests accept (tests/schema)."""
    extras = sbom_script.project()["optional-dependencies"]
    named = {canonicalize_name(Requirement(r).name) for e in extras.values() for r in e}
    assert not {n for n in named if n.startswith("cyclonedx")}


def _package_job() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["package"]["steps"]
    return steps


@pytest.mark.req("TER-SBM-002")
def test_ci_writes_the_sbom_of_the_tested_install_checks_it_and_uploads_it() -> None:
    steps = _package_job()
    names = [s.get("name") for s in steps]
    install = steps[names.index("Install wheel in clean environment")]
    write = steps[names.index("Write the SBOM of the tested install")]
    check = steps[names.index("Check the SBOM")]
    upload = steps[names.index("Upload the SBOM")]
    # The smoke-tested environment, with no pip of its own, is the one scanned.
    assert "venv --without-pip /tmp/ter-wheel-test" in install["run"]
    assert names.index("Smoke-test the installed wheel") < names.index(write["name"])
    assert "scripts/sbom.py --env /tmp/ter-wheel-test" in write["run"]
    assert check["env"] == {
        "TER_SBOM": "dist/ter.cdx.json",
        "TER_SBOM_ENV": "/tmp/ter-wheel-test",
    }
    assert "tests/sbom" in check["run"]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"] == {"name": "sbom", "path": "dist/ter.cdx.json"}


@pytest.mark.req("TER-SBM-002")
@pytest.mark.skipif(
    not os.environ.get("TER_SBOM") or not os.environ.get("TER_SBOM_ENV"),
    reason="set TER_SBOM and TER_SBOM_ENV (CI's package job does)",
)
def test_the_published_sbom_matches_the_tested_install() -> None:
    sbom = json.loads(Path(os.environ["TER_SBOM"]).read_text(encoding="utf-8"))
    python = sbom_script._python_in(Path(os.environ["TER_SBOM_ENV"]))
    installed = sbom_script.installed_in(python)
    assert list(sbom_script.problems(sbom, installed, sbom_script.project())) == []
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.6"
