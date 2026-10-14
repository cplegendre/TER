"""Write TER's software bill of materials (SBOM), a CycloneDX 1.6 JSON file.

The SBOM lists what installing TER actually installs: the wheel is put in a
fresh virtual environment with no pip of its own, and every distribution in
that environment, with its exact version, package URL and declared licence,
is a component. CI's package job runs this on the wheel it built and uploads
the file as the ``sbom`` artifact of every run; ``tests/sbom`` checks it::

    python scripts/sbom.py                       # build the wheel, write dist/ter.cdx.json
    python scripts/sbom.py --wheel dist/x.whl    # describe a wheel already built
    python scripts/sbom.py --out sbom.json

Needs network access: the wheel's dependencies and the SBOM tool
(``cyclonedx-bom``, in the range ``constraints/ci.txt`` sets) are installed
into environments of their own. The tool stays out of the ``dev`` extra on
purpose: its JSON validation installs jsonschema's format checkers, which
change what the schema contract tests accept. Versions resolve on the day it runs, inside the ranges
pyproject.toml declares, which is why the SBOM is a build output rather than
a committed file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dist" / "ter.cdx.json"
CONSTRAINTS = ROOT / "constraints" / "ci.txt"


def _run(*command: str | Path) -> None:
    subprocess.run([str(part) for part in command], check=True, cwd=ROOT)


def _python_in(env: Path) -> Path:
    if os.name == "nt":
        return env / "Scripts" / "python.exe"
    return env / "bin" / "python"


def write_sbom(out: Path, wheel: Path | None = None) -> Path:
    """Write the SBOM of ``wheel`` (built from this checkout when absent)."""
    with tempfile.TemporaryDirectory(prefix="ter-sbom-") as scratch:
        work = Path(scratch)
        if wheel is None:
            _run(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", work)
            (wheel,) = work.glob("*.whl")
        env = work / "env"
        # No pip in the environment, so the SBOM lists only what TER installs.
        _run(sys.executable, "-m", "venv", "--without-pip", env)
        python = _python_in(env)
        _run(sys.executable, "-m", "pip", "--python", python, "install", "-q", wheel)
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
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", type=Path, help="a built TER wheel to describe")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="SBOM file")
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve() if args.wheel else None
    print(write_sbom(args.out.resolve(), wheel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
