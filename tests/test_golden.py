"""End-to-end regression tests on the real dataset."""

import json
from pathlib import Path

from jenius import config
from jenius.evaluation import evaluate
from jenius.report import knowledge_base, write_csv

GOLDEN = Path(__file__).parent / "golden" / "output_anomalies.csv"


def test_output_matches_golden_file(analysis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "out.csv"
    write_csv(analysis.anomalies, out)
    assert out.read_bytes() == GOLDEN.read_bytes()


def test_output_has_sample_format_columns(analysis) -> None:  # type: ignore[no-untyped-def]
    sample = (config.DATA_DIR / "sample_output_format_v2.csv").read_text().splitlines()[0]
    assert GOLDEN.read_text().splitlines()[0] == sample


def test_eval_is_reproducible_and_correct() -> None:
    report = evaluate(runs=2)
    assert report.reproducible
    assert report.failures == []


def test_committed_dashboard_data_is_fresh(analysis) -> None:  # type: ignore[no-untyped-def]
    committed = json.loads((config.WEB_DIR / "data" / "kb.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(knowledge_base(analysis)))
    assert committed == fresh, "web/data/kb.json is stale: run `python -m jenius build`"
