from pathlib import Path

import pytest

from jenius.cli import main


def test_detect_writes_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "anomalies.csv"
    assert main(["detect", "-o", str(out)]) == 0
    assert out.read_text().count("\n") == 7  # header + 6 anomalies
    assert "4 justified, 2 need review" in capsys.readouterr().out


def test_detect_custom_thresholds(tmp_path: Path) -> None:
    out = tmp_path / "anomalies.csv"
    main(["detect", "--own", "10", "--peer", "10", "-o", str(out)])
    assert out.read_text().count("\n") > 7


def test_ask(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", "what", "does", "N002", "say"]) == 0
    assert "festival" in capsys.readouterr().out


def test_eval(tmp_path: Path) -> None:
    assert main(["eval", "-o", str(tmp_path / "r.json")]) == 0
