"""
eval_harness.py — Lightweight reproducibility & correctness eval
================================================================
Runs solution.py three times and diffs the outputs.
Also runs a small labelled eval set to verify verdict correctness.

Usage:
  python3 eval_harness.py
"""

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

OUTPUT = Path("/Users/jahaansuthar/Downloads/FreightTiger/output_anomalies.csv")
SOLUTION = Path("/Users/jahaansuthar/Downloads/FreightTiger/solution.py")

# ── Labelled eval set (ground truth from manual inspection of notes + data) ──
LABELLED = [
    # (route, week_of, expected_flagged, expected_note_id)
    ("Ahmedabad-Mumbai",  "2025-01-20", "No (justified)", "N002"),
    ("Chennai-Bangalore", "2025-02-24", "No (justified)", "N001"),
    ("Chennai-Bangalore", "2025-03-03", "No (justified)", "N001"),
    ("Delhi-Jaipur",      "2024-11-11", "Yes",            ""),
    ("Delhi-Jaipur",      "2024-11-18", "Yes",            ""),
]

def run_solution() -> str:
    """Run solution.py and return SHA-256 of the output CSV."""
    result = subprocess.run(
        [sys.executable, str(SOLUTION)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"solution.py failed:\n{result.stderr}")
    content = OUTPUT.read_bytes()
    return hashlib.sha256(content).hexdigest()


def run_eval(rows: list[dict]) -> dict:
    """Score output against labelled ground truth."""
    row_map = {(r["route"], r["week_of"]): r for r in rows}
    passed = 0
    failed = []
    for route, week, exp_flag, exp_note in LABELLED:
        row = row_map.get((route, week))
        if row is None:
            failed.append(f"MISSING  {route} @ {week}")
            continue
        ok_flag = row["flagged"] == exp_flag
        ok_note = row["matched_note_id"] == exp_note
        if ok_flag and ok_note:
            passed += 1
        else:
            failed.append(
                f"MISMATCH {route} @ {week}: "
                f"flagged={row['flagged']!r} (expected {exp_flag!r}), "
                f"note_id={row['matched_note_id']!r} (expected {exp_note!r})"
            )
    return {"passed": passed, "total": len(LABELLED), "failures": failed}


def main():
    print("=" * 60)
    print("FreightTiger AI — Eval Harness")
    print("=" * 60)

    # ── 3 runs, compare hashes ──
    print("\n[1/2] Reproducibility check (3 independent runs)…\n")
    hashes = []
    for i in range(1, 4):
        h = run_solution()
        hashes.append(h)
        print(f"  Run {i}: SHA-256 = {h[:16]}…")

    if len(set(hashes)) == 1:
        print(f"\n  ✅ All 3 runs produced identical output (hash={hashes[0][:16]}…)")
        repro_ok = True
    else:
        print("\n  ❌ Runs produced DIFFERENT output — non-determinism detected!")
        repro_ok = False

    # ── Correctness eval ──
    print("\n[2/2] Correctness eval against labelled ground truth…\n")
    with open(OUTPUT, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("route")]

    result = run_eval(rows)
    print(f"  Passed: {result['passed']}/{result['total']}")
    for fail in result["failures"]:
        print(f"  ❌ {fail}")
    if not result["failures"]:
        print("  ✅ All labelled cases match expected verdicts")

    # ── Summary ──
    print("\n" + "=" * 60)
    score = result["passed"] / result["total"] * 100
    print(f"Reproducibility: {'✅ PASS' if repro_ok else '❌ FAIL'}")
    print(f"Correctness:     {score:.0f}% ({result['passed']}/{result['total']} cases)")
    print("=" * 60)

    # Write JSON report
    report = {
        "reproducibility": {"pass": repro_ok, "hashes": [h[:16] for h in hashes]},
        "correctness": result,
        "overall_score_pct": round(score, 1)
    }
    report_path = Path("/Users/jahaansuthar/Downloads/FreightTiger/eval_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {report_path}")


if __name__ == "__main__":
    main()
