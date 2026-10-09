"""A small synthetic repository with a layered architecture, for the L3
change-surface and boundary tests.

The import graph at the start commit::

    adapters/web.py -> service/checkout.py -> domain/pricing.py -> domain/model.py
    reports/summary.py (imports nothing of the project)

``pyproject.toml`` declares two import-linter contracts: the layers
adapters > service > domain, and a domain that never imports ``requests``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SHOP: dict[str, str] = {
    "README.md": "# Shop\n\nA tiny shop.\n",
    "pyproject.toml": (
        "[project]\n"
        'name = "shop"\n'
        "\n"
        "[tool.importlinter]\n"
        'root_packages = ["app"]\n'
        "\n"
        "[[tool.importlinter.contracts]]\n"
        'id = "layers"\n'
        'name = "Layers point inward"\n'
        'type = "layers"\n'
        'layers = ["app.adapters", "app.service", "app.domain"]\n'
        "\n"
        "[[tool.importlinter.contracts]]\n"
        'id = "pure-domain"\n'
        'name = "The domain does no IO"\n'
        'type = "forbidden"\n'
        'source_modules = ["app.domain"]\n'
        'forbidden_modules = ["requests"]\n'
    ),
    "src/app/__init__.py": "",
    "src/app/domain/__init__.py": "",
    "src/app/domain/model.py": (
        "class Order:\n    pass\n\n\ndef order_total(items):\n    return sum(items)\n"
    ),
    "src/app/domain/pricing.py": (
        "from app.domain.model import order_total\n"
        "\n"
        "\n"
        "def price_with_tax(items):\n"
        "    return order_total(items) * 1.2\n"
    ),
    "src/app/service/__init__.py": "",
    "src/app/service/checkout.py": (
        "from app.domain.pricing import price_with_tax\n"
        "\n"
        "\n"
        "def checkout(items):\n"
        "    return price_with_tax(items)\n"
    ),
    "src/app/adapters/__init__.py": "",
    "src/app/adapters/web.py": (
        "from app.service.checkout import checkout\n"
        "\n"
        "\n"
        "def handle(request):\n"
        "    return checkout(request)\n"
    ),
    "src/app/reports/__init__.py": "",
    "src/app/reports/summary.py": (
        "def monthly_summary(rows):\n    return len(rows)\n"
    ),
    "tests/test_pricing.py": (
        "from app.domain.pricing import price_with_tax\n"
        "\n"
        "\n"
        "def test_tax():\n"
        "    assert price_with_tax([10]) == 12\n"
    ),
    "tests/test_checkout.py": (
        "from app.service.checkout import checkout\n"
        "\n"
        "\n"
        "def test_checkout():\n"
        "    assert checkout([10]) == 12\n"
    ),
    "tests/test_summary.py": (
        "from app.reports.summary import monthly_summary\n"
        "\n"
        "\n"
        "def test_summary():\n"
        "    assert monthly_summary([1]) == 1\n"
    ),
}

#: Where the session that worked on the shop ran: its tools name absolute
#: paths under this directory.
SESSION_ROOT = "/home/dev/shop"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T09:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T09:00:00+00:00",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def at(path: str) -> str:
    """The absolute path the session's tools used for a repository path."""
    return f"{SESSION_ROOT}/{path}"


def shop_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    """Write the shop under ``root`` and commit it: the session's start commit."""
    root.mkdir(parents=True, exist_ok=True)
    for path, text in (SHOP if files is None else files).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = {**os.environ, **GIT_ENV, "HOME": str(root.parent)}
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "start"],
    ):
        subprocess.run(["git", *args], cwd=root, env=env, check=True)
    return root
