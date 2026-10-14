"""The smoke suite: TER's commands run as a user runs them (TER-TST-006).

Each test starts the real console scripts (``ter`` and ``python -m ter``) in
a fresh process, in an empty working directory, and checks the exit code,
the files written and, where the format has a JSON Schema, that they conform.

By default the commands come from the environment running pytest, with
``src`` first on ``PYTHONPATH`` so this checkout is what runs. Set
``TER_SMOKE_BIN`` to the scripts folder of another environment (CI points it
at a clean venv holding only the built wheel) and the suite runs that install
instead, with no ``PYTHONPATH``, so a file left out of the wheel fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "sample_sessions" / "example_session.jsonl"
FIXTURE = REPO / "tests" / "fixtures" / "sample_session.jsonl"
GOLDEN_SESSIONS = REPO / "tests" / "golden" / "sessions"
HOOKS = REPO / "tests" / "fixtures" / "hooks"
POLICIES = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"
TIMEOUT = 300

pytestmark = pytest.mark.smoke


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    def ok(self) -> Result:
        assert self.code == 0, (
            f"exit {self.code}\nstdout:\n{self.out}\nstderr:\n{self.err}"
        )
        return self


class Cli:
    """Runs TER's console scripts in ``cwd`` as separate processes."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        installed = os.environ.get("TER_SMOKE_BIN")
        self.bin = Path(installed or sysconfig.get_path("scripts"))
        self.installed = bool(installed)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        if not installed:
            env["PYTHONPATH"] = str(REPO / "src")
        self.env = env

    def script(self, name: str) -> str:
        """The path of an installed console script."""
        suffix = ".exe" if os.name == "nt" else ""
        return str(self.bin / f"{name}{suffix}")

    @property
    def python(self) -> str:
        if not self.installed:
            return sys.executable
        return self.script("python")

    def run(self, argv: list[str], stdin: str | None = None) -> Result:
        done = subprocess.run(
            argv,
            cwd=self.cwd,
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT,
            check=False,
        )
        return Result(done.returncode, done.stdout, done.stderr)

    def ter(self, *args: str, stdin: str | None = None) -> Result:
        """The TER 3 ``ter`` console script."""
        return self.run([self.script("ter"), *args], stdin)

    def ter4(self, *args: str, stdin: str | None = None) -> Result:
        """TER 4: ``python -m ter``."""
        return self.run([self.python, "-m", "ter", *args], stdin)

    def ter_req(self, *args: str) -> Result:
        return self.run([self.script("ter-req"), *args])


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("smoke")


@pytest.fixture(scope="module")
def cli(workdir: Path) -> Cli:
    return Cli(workdir)
