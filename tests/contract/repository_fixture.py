"""A small synthetic repository shared by the repository evidence tests.

``REPO`` is the content; :func:`write_repo` lays it out under a directory and
:func:`git_repo` also makes it a Git working tree with one commit. ``IMPORTS``
is what the in-memory fake is told each Python file imports (the absolute
modules its import statements name), matching what the real engines read.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

REPO: dict[str, str] = {
    "README.md": "Add numbers with pkg.core.add(a, b).\n",
    "src/pkg/__init__.py": '"""The package."""\n',
    "src/pkg/core.py": (
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "\n"
        "class Calc:\n"
        "    def total(self, xs):\n"
        "        return sum(add(x, 0) for x in xs)\n"
    ),
    "src/pkg/util.py": (
        "from .core import add\n\n\ndef twice(x):\n    return add(x, x)\n"
    ),
    "tests/helpers.py": "from pkg.core import Calc\n",
    "tests/test_both.py": (
        "from pkg import (\n"
        "    core,  # the core\n"
        "    util,\n"
        ")\n"
        "\n"
        "\n"
        "def test_both():\n"
        "    assert core.add(1, 1) == util.twice(1)\n"
    ),
    "tests/test_core.py": (
        "from pkg.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    ),
    "tests/test_other.py": "import json\n\n\ndef test_json():\n    assert json\n",
    "tests/test_util.py": (
        "import pkg.util as u\n\n\ndef test_twice():\n    assert u.twice(2) == 4\n"
    ),
    "tests/unit/core_test.py": "from pkg.core import Calc\n",
}

IMPORTS: dict[str, tuple[str, ...]] = {
    "src/pkg/util.py": ("pkg.core", "pkg.core.add"),
    "tests/helpers.py": ("pkg.core", "pkg.core.Calc"),
    "tests/test_both.py": ("pkg", "pkg.core", "pkg.util"),
    "tests/test_core.py": ("pkg.core", "pkg.core.add"),
    "tests/test_other.py": ("json",),
    "tests/test_util.py": ("pkg.util",),
    "tests/unit/core_test.py": ("pkg.core", "pkg.core.Calc"),
}

#: Expected test modules per source module (direct import statements only).
TESTS_OF: dict[str, tuple[str, ...]] = {
    "src/pkg/core.py": (
        "tests/test_both.py",
        "tests/test_core.py",
        "tests/unit/core_test.py",
    ),
    "src/pkg/util.py": ("tests/test_both.py", "tests/test_util.py"),
    "src/pkg/__init__.py": (
        "tests/test_both.py",
        "tests/test_core.py",
        "tests/test_util.py",
        "tests/unit/core_test.py",
    ),
    "tests/helpers.py": (),
}

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def write_repo(root: Path, files: Mapping[str, str] = REPO) -> Path:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    return root


def git(root: Path, *args: str, date: str = "2026-01-02T03:04:05+00:00") -> str:
    """Run git in ``root`` with a fixed identity and date."""
    env = {**os.environ, **GIT_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    run = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout


def git_repo(root: Path, files: Mapping[str, str] = REPO) -> Path:
    """``files`` under ``root``, committed once as a Git working tree."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    write_repo(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root
