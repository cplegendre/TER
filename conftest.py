"""Repository-wide pytest configuration.

Registers the requirement-traceability plugin, which adds ``--req-trace``
(see docs/ter4/requirements.md).

Tests marked ``embeddings`` run TER 3's real sentence-transformers model.
Without the ``embeddings`` extra they are skipped with a reason that says how
to install it, so the documented ``.[dev]`` setup gives a green suite. Set
``TER_REQUIRE_EMBEDDINGS=1`` (CI does) to run them regardless, so a missing
model fails loudly instead of hiding behind a skip.

Tests run in a random order (TER-TST-012), so one test leaning on state
another left behind fails instead of passing by luck. Modules are shuffled,
then the classes in a module, then the tests in a class, so module and class
fixtures are still set up once. The header names the seed; rerun a failing
order with ``--test-order-seed=N`` (or ``TER_TEST_ORDER_SEED=N``), and run
in file order with ``--test-order=declared``. ``--ff`` and ``--nf`` choose
their own order (``python dev.py fast`` runs last failures first), so with
either of them nothing is shuffled.

``--fuzz-scale=N`` runs the fuzzers in ``tests/resilience`` with N times
their examples (``python dev.py fuzz`` uses 20).
"""

from __future__ import annotations

import importlib.util
import os
import random
from collections.abc import Iterable

import pytest

pytest_plugins = ("ter.adapters.driving.pytest_req", "pytester")

_EMBEDDINGS_SKIP = pytest.mark.skip(
    reason=(
        "needs the embeddings extra: "
        "python -m pip install -c constraints/dev.txt -e '.[dev,embeddings]'"
    )
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ter", "TER test order")
    group.addoption(
        "--test-order",
        choices=("random", "declared"),
        default="random",
        help="run tests shuffled by module, class and test (default) or in file order",
    )
    group.addoption(
        "--fuzz-scale",
        type=int,
        default=None,
        help="multiply the examples of the tests/resilience fuzzers (TER_FUZZ_SCALE)",
    )
    group.addoption(
        "--test-order-seed",
        type=int,
        default=None,
        help="seed for the random order (default: TER_TEST_ORDER_SEED or a new one)",
    )


def pytest_configure(config: pytest.Config) -> None:
    scale = config.getoption("--fuzz-scale")
    if scale is not None:
        os.environ["TER_FUZZ_SCALE"] = str(scale)
    seed = config.getoption("--test-order-seed")
    if seed is None:
        from_env = os.environ.get("TER_TEST_ORDER_SEED")
        seed = int(from_env) if from_env else random.SystemRandom().randrange(10**6)
    config.stash[_ORDER_SEED] = seed


def _cache_orders(config: pytest.Config) -> str | None:
    """The cache option that orders the run, when one is given."""
    for flag, dest in (("--ff", "failedfirst"), ("--nf", "newfirst")):
        if getattr(config.option, dest, False):
            return flag
    return None


def pytest_report_header(config: pytest.Config) -> str:
    if config.getoption("--test-order") == "declared":
        return "test order: declared"
    flag = _cache_orders(config)
    if flag is not None:
        return f"test order: as {flag} orders it (not shuffled)"
    seed = config.stash[_ORDER_SEED]
    return f"test order: random, seed {seed} (rerun with --test-order-seed={seed})"


_ORDER_SEED = pytest.StashKey[int]()


def _groups(items: Iterable[pytest.Item], key_of: str) -> dict[str, list[pytest.Item]]:
    groups: dict[str, list[pytest.Item]] = {}
    for item in items:
        if key_of == "module":
            key = item.nodeid.split("::", 1)[0]
        else:
            parts = item.nodeid.split("::")
            key = "::".join(parts[:-1])
        groups.setdefault(key, []).append(item)
    return groups


def shuffled(items: list[pytest.Item], seed: int) -> list[pytest.Item]:
    """``items`` shuffled by module, then class, then test, reproducibly."""
    rng = random.Random(seed)
    order: list[pytest.Item] = []
    modules = list(_groups(items, "module").values())
    rng.shuffle(modules)
    for module in modules:
        classes = list(_groups(module, "class").values())
        rng.shuffle(classes)
        for tests in classes:
            tests = list(tests)
            rng.shuffle(tests)
            order.extend(tests)
    return order


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--test-order") == "random" and not _cache_orders(config):
        items[:] = shuffled(items, config.stash[_ORDER_SEED])
    _skip_embeddings_without_the_extra(items)


def _embeddings_installed() -> bool:
    return importlib.util.find_spec("sentence_transformers") is not None


def _skip_embeddings_without_the_extra(items: list[pytest.Item]) -> None:
    if os.environ.get("TER_REQUIRE_EMBEDDINGS") == "1" or _embeddings_installed():
        return
    for item in items:
        if item.get_closest_marker("embeddings"):
            item.add_marker(_EMBEDDINGS_SKIP)
