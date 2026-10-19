"""Reproducibility and correctness checks against hand-labelled cases."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .pipeline import run
from .report import write_csv

# (route, week_of, expected verdict, expected note id), labelled by reading the
# notes and the raw shipment data by hand.
LABELLED: tuple[tuple[str, str, str, str], ...] = (
    ("Ahmedabad-Mumbai", "2025-01-20", "No (justified)", "N002"),
    ("Chennai-Bangalore", "2025-02-24", "No (justified)", "N001"),
    ("Chennai-Bangalore", "2025-03-03", "No (justified)", "N001"),
    ("Delhi-Jaipur", "2024-11-11", "Yes", ""),
    ("Delhi-Jaipur", "2024-11-18", "Yes", ""),
)


@dataclass
class EvalReport:
    hashes: list[str]
    passed: int
    total: int
    failures: list[str] = field(default_factory=list)

    @property
    def reproducible(self) -> bool:
        return len(set(self.hashes)) == 1

    @property
    def ok(self) -> bool:
        return self.reproducible and not self.failures

    def as_dict(self) -> dict[str, object]:
        return {
            "reproducibility": {"pass": self.reproducible, "hashes": [h[:16] for h in self.hashes]},
            "correctness": {"passed": self.passed, "total": self.total, "failures": self.failures},
            "overall_score_pct": round(self.passed / self.total * 100, 1),
        }


def evaluate(runs: int = 3) -> EvalReport:
    hashes = []
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(runs):
            path = Path(tmp) / f"run{i}.csv"
            analysis = run()
            write_csv(analysis.anomalies, path)
            hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
            rows = {(f.rw.route, f.rw.week_of): f.csv_row() for f in analysis.anomalies}

    failures = []
    for route, week, verdict, note_id in LABELLED:
        row = rows.get((route, week))
        if row is None:
            failures.append(f"MISSING  {route} @ {week}")
        elif row["flagged"] != verdict or row["matched_note_id"] != note_id:
            failures.append(
                f"MISMATCH {route} @ {week}: got {row['flagged']!r}/{row['matched_note_id']!r}, "
                f"expected {verdict!r}/{note_id!r}"
            )
    return EvalReport(hashes, len(LABELLED) - len(failures), len(LABELLED), failures)
