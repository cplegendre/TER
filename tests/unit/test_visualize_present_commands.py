"""End-to-end tests for the `ter visualize` and `ter present` subcommands."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter_calculator import embedding_cache
from ter_calculator.cli import main

SAMPLE = (
    Path(__file__).resolve().parents[2] / "sample_sessions" / "example_session.jsonl"
)


class _Encoding:
    def encode(self, text: str) -> range:
        return range(RegexTokenizer().count(text))


@pytest.fixture(autouse=True)
def offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_cache, "_TIKTOKEN_ENC", _Encoding())
    monkeypatch.setitem(
        embedding_cache._MODEL_CACHE,
        embedding_cache.DEFAULT_MODEL_NAME,
        HashingEmbedder(embedding_cache.EMBEDDING_DIM),
    )


@pytest.fixture
def session(tmp_path: Path) -> Path:
    return Path(shutil.copy(SAMPLE, tmp_path / "demo.jsonl"))


class TestVisualize:
    def test_writes_every_chart_to_the_output_dir(
        self, session: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "charts"
        assert main(["visualize", str(session), "-o", str(out)]) == 0
        written = sorted(p.stem for p in out.glob("*.svg"))
        assert {"key_metrics", "phase_scores", "composition"} <= set(written)
        assert all(p.read_text().startswith("<svg") for p in out.glob("*.svg"))
        assert "Generated" in capsys.readouterr().err

    def test_defaults_to_a_directory_named_after_the_session(
        self, session: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["--quiet", "visualize", str(session)]) == 0
        assert list((tmp_path / "demo_charts").glob("*.svg"))

    def test_chart_filter_selects_named_charts(
        self, session: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "charts"
        code = main(
            [
                "--quiet",
                "visualize",
                str(session),
                "-o",
                str(out),
                "--charts",
                "key_metrics, phase_scores",
            ]
        )
        assert code == 0
        assert sorted(p.stem for p in out.glob("*.svg")) == [
            "key_metrics",
            "phase_scores",
        ]

    def test_unknown_chart_filter_fails_and_lists_available(
        self, session: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            ["visualize", str(session), "-o", str(tmp_path), "--charts", "nope"]
        )
        assert code == 1
        assert "Available:" in capsys.readouterr().err

    def test_requires_a_session_or_latest(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["visualize"]) == 1
        assert "--latest" in capsys.readouterr().err

    def test_latest_uses_the_newest_session_in_a_directory(
        self, session: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "charts"
        assert main(["visualize", str(tmp_path), "--latest", "-o", str(out)]) == 0
        assert "Using latest session" in capsys.readouterr().err
        assert list(out.glob("*.svg"))


class TestPresent:
    def test_writes_a_marp_deck_next_to_the_session(
        self, session: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["present", str(session)]) == 0
        deck = session.with_suffix(".ter-slides.md")
        assert deck.read_text().startswith("---\nmarp: true")
        assert "marp-cli" in capsys.readouterr().err

    def test_output_path_and_latest(self, session: Path, tmp_path: Path) -> None:
        target = tmp_path / "deck.md"
        assert (
            main(["--quiet", "present", str(tmp_path), "--latest", "-o", str(target)])
            == 0
        )
        assert "marp: true" in target.read_text()

    def test_requires_a_session_or_latest(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["present"]) == 1
        assert "--latest" in capsys.readouterr().err
