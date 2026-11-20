"""Smoke tests for ``python -m jisu.bench.memory``."""

from __future__ import annotations
from pathlib import Path

import pytest

from jisu.bench.memory.__main__ import EXIT_OK, EXIT_NOTHING_RAN, main


def test_run_save_list_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the bundled dataset on json, save, list and show the report."""
    out = tmp_path / "out"
    assert main(["--subjects", "json", "--repeat", "1", "--out", str(out), "--detail", "--quiet"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "hit@5" in printed and "short_word" in printed and "[json]" in printed
    [saved] = list(out.glob("*.json"))

    assert main(["--list", "--out", str(out)]) == EXIT_OK
    assert saved.name in capsys.readouterr().out
    assert main(["--show", str(saved)]) == EXIT_OK
    assert "json" in capsys.readouterr().out


def test_no_save_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--no-save writes nothing; bad inputs exit with 2."""
    out = tmp_path / "out"
    assert main(["--subjects", "json", "--repeat", "1", "--no-save", "--quiet", "--out", str(out)]) == EXIT_OK
    assert not out.exists()
    assert main(["--dataset", str(tmp_path / "missing.toml"), "--subjects", "json"]) == EXIT_NOTHING_RAN
    assert main(["--subjects", "json", "--k", "0"]) == EXIT_NOTHING_RAN
    assert main(["--show", str(tmp_path / "missing.json")]) == EXIT_NOTHING_RAN
    assert "error:" in capsys.readouterr().err
