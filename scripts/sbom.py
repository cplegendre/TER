"""Write TER's software bill of materials (SBOM), a CycloneDX 1.6 JSON file.

The SBOM lists what installing TER actually installs: every distribution in
an environment holding the wheel and no pip of its own, with its exact
version, package URL and declared licence. The SBOM is then checked against
that environment (:func:`problems`), and a mismatch exits 1. CI's package
job runs it on the clean environment it smoke-tests, so the SBOM names the
versions that were tested, and uploads the file as the ``sbom`` artifact of
every run::

    python scripts/sbom.py                       # build the wheel, write dist/ter.cdx.json
    python scripts/sbom.py --wheel dist/x.whl    # a wheel already built
    python scripts/sbom.py --env /tmp/ter-env    # an environment that already holds it
    python scripts/sbom.py --out sbom.json

Needs network access: the wheel's dependencies and the SBOM tool
(``cyclonedx-bom``, in the range ``constraints/ci.txt`` sets) are installed
into environments of their own. The tool stays out of the ``dev`` extra on
purpose: its JSON validation installs jsonschema's format checkers, which
change what the schema contract tests accept. Versions resolve on the day it
runs, inside the ranges pyproject.toml declares, which is why the SBOM is a
build output rather than a committed file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dist" / "ter.cdx.json"
CONSTRAINTS = ROOT / "constraints" / "ci.txt"
ROOT_REF = "root-component"
#: Distributions whose licence the SBOM cannot carry, with why. Only
#: shrinks: a new one fails the check and is reviewed before it ships.
LICENCE_NOT_IN_SBOM = {
    "tiktoken": "its metadata holds the full MIT licence text, not an identifier",
}
#: Build and test tooling, which TER never installs.
TOOLING = {"pip", "setuptools", "wheel", "pytest", "build"}

#: Run inside the scanned environment (``python -I``): its distributions and
#: the licence fields of their metadata.
_LIST_INSTALLED = """
import importlib.metadata as m, json
print(json.dumps([
    {"name": d.metadata["Name"], "version": d.version,
     "licences": [v for v in (d.metadata.get("License-Expression"),
                              d.metadata.get("License")) if v]
                 + [c for c in d.metadata.get_all("Classifier") or []
                    if c.startswith("License ::")]}
    for d in m.distributions()
]))
"""


@dataclass(frozen=True)
class Installed:
    """One distribution in the scanned environment, from its metadata."""

    name: str
    version: str
    licences: tuple[str, ...]

    @property
    def key(self) -> str:
        return canonicalize_name(self.name)


def installed_in(python: Path) -> list[Installed]:
    out = subprocess.run(
        [str(python), "-I", "-c", _LIST_INSTALLED],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [
        Installed(d["name"], d["version"], tuple(d["licences"]))
        for d in json.loads(out)
    ]


def project() -> dict[str, Any]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    table: dict[str, Any] = tomllib.loads(text)["project"]
    return table


def _licence_values(component: Mapping[str, Any]) -> list[str]:
    values = []
    for entry in component.get("licenses", []):
        if "expression" in entry:
            values.append(str(entry["expression"]))
        licence = entry.get("license", {})
        values += [str(licence[k]) for k in ("id", "name") if k in licence]
    return values


def _declares(value: str, declared: tuple[str, ...]) -> bool:
    """``value`` is one of the distribution's own licence fields, or names
    the licence one of its ``License ::`` classifiers names."""
    if value in declared:
        return True
    family = value.split("-")[0].lower()
    return any(
        d.startswith("License ::") and family in d.lower().split("::")[-1]
        for d in declared
    )


def problems(
    sbom: Mapping[str, Any], installed: list[Installed], meta: Mapping[str, Any]
) -> Iterator[str]:
    """Every way ``sbom`` misdescribes the environment holding ``installed``."""
    root = sbom.get("metadata", {}).get("component", {})
    if (root.get("name"), root.get("version")) != (meta["name"], meta["version"]):
        yield f"root component is {root.get('name')} {root.get('version')}"
    if _licence_values(root) != [meta["license"]]:
        yield f"root licence is {_licence_values(root)}, not {meta['license']}"

    components = {canonicalize_name(c["name"]): c for c in sbom.get("components", [])}
    present = {i.key: i for i in installed if i.key != canonicalize_name(meta["name"])}
    for name in sorted(present.keys() - components.keys()):
        yield f"{name} is installed but not in the SBOM"
    for name in sorted(components.keys() - present.keys()):
        yield f"{name} is in the SBOM but not installed"
    for name in sorted(TOOLING & components.keys()):
        yield f"{name} is tooling, which TER does not install"

    for name, component in sorted(components.items()):
        dist = present.get(name)
        version = component.get("version")
        if dist is not None and version != dist.version:
            yield f"{name} is {version} in the SBOM but {dist.version} installed"
        if str(component.get("purl", "")).lower() != f"pkg:pypi/{name}@{version}":
            yield f"{name} has package URL {component.get('purl')}"
        values = _licence_values(component)
        if name in LICENCE_NOT_IN_SBOM:
            if values:
                yield f"{name} now has a licence: take it off LICENCE_NOT_IN_SBOM"
        elif not values:
            yield f"{name} has no licence in the SBOM"
        elif dist is not None:
            for value in values:
                if not _declares(value, dist.licences):
                    yield f"{name} licence {value} is not what it declares"

    refs = {c.get("bom-ref") for c in components.values()} | {ROOT_REF}
    graph = {
        d["ref"]: list(d.get("dependsOn", [])) for d in sbom.get("dependencies", [])
    }
    for ref, targets in sorted(graph.items()):
        for target in [ref, *targets]:
            if target not in refs:
                yield f"dependency graph names {target}, which is not a component"
    direct = {c.get("bom-ref"): n for n, c in components.items()}
    wanted = {canonicalize_name(Requirement(r).name) for r in meta["dependencies"]}
    for requirement in map(Requirement, meta["dependencies"]):
        name = canonicalize_name(requirement.name)
        component = components.get(name)
        if component is not None and not requirement.specifier.contains(
            str(component.get("version"))
        ):
            yield f"{name} {component.get('version')} is outside {requirement}"
    if {direct.get(r) for r in graph.get(ROOT_REF, [])} != wanted:
        yield "TER's direct dependencies in the graph are not pyproject.toml's"
    reached: set[str] = set()
    todo = [ROOT_REF]
    while todo:
        ref = todo.pop()
        if ref not in reached:
            reached.add(ref)
            todo.extend(graph.get(ref, []))
    for ref in sorted(r for r in refs - reached if r):
        yield f"{ref} is listed but nothing TER installs depends on it"


def _run(*command: str | Path) -> None:
    subprocess.run([str(part) for part in command], check=True, cwd=ROOT)


def _python_in(env: Path) -> Path:
    if os.name == "nt":
        return env / "Scripts" / "python.exe"
    return env / "bin" / "python"


def write_sbom(out: Path, wheel: Path | None = None, env: Path | None = None) -> Path:
    """Write the SBOM of ``env`` (an environment holding the wheel and no pip),
    or of a fresh one holding ``wheel`` (built from this checkout when absent),
    and check it against that environment."""
    with tempfile.TemporaryDirectory(prefix="ter-sbom-") as scratch:
        work = Path(scratch)
        if env is None:
            if wheel is None:
                _run(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", work)
                (wheel,) = work.glob("*.whl")
            env = work / "env"
            _run(sys.executable, "-m", "venv", "--without-pip", env)
            _run(
                sys.executable,
                "-m",
                "pip",
                "--python",
                _python_in(env),
                "install",
                "-q",
                wheel,
            )
        python = _python_in(env)
        tools = work / "tools"
        _run(sys.executable, "-m", "venv", tools)
        tool = _python_in(tools)
        _run(tool, "-m", "pip", "install", "-q", "-c", CONSTRAINTS, "cyclonedx-bom")
        out.parent.mkdir(parents=True, exist_ok=True)
        _run(
            tool,
            "-m",
            "cyclonedx_py",
            "environment",
            "--pyproject",
            ROOT / "pyproject.toml",
            "--spec-version",
            "1.6",
            "--output-reproducible",
            "--output-format",
            "JSON",
            "--validate",
            "--output-file",
            out,
            python,
        )
        found = list(
            problems(
                json.loads(out.read_text(encoding="utf-8")),
                installed_in(python),
                project(),
            )
        )
    if found:
        raise SystemExit("the SBOM does not match the install:\n" + "\n".join(found))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wheel", type=Path, help="a built TER wheel to describe")
    source.add_argument("--env", type=Path, help="an environment holding the wheel")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="SBOM file")
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve() if args.wheel else None
    env = args.env.resolve() if args.env else None
    print(write_sbom(args.out.resolve(), wheel, env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
