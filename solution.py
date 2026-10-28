"""
FreightTiger SWE Intern (AI) — Case Study Solution  v3
=======================================================
Author  : Jahaan Suthar

Regenerates output_anomalies.csv from the case-study data with the Jenius anomaly engine
(jenius/analysis.py). The engine is deterministic: zero LLM calls, byte-identical output every run.

Usage:
  python3 solution.py
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jenius import analysis  # noqa: E402
from jenius.ingest import parse_notes, parse_shipments  # noqa: E402

DATA_DIR = ROOT / "AI Intern Case Study"
SHIPMENT_FILE = DATA_DIR / "shipment_records.csv"
NOTES_FILE = DATA_DIR / "context_notes.csv"
OUTPUT_FILE = ROOT / "output_anomalies.csv"
LOG_FILE = ROOT / "token_cost_log.json"


def load_case_study() -> tuple[list[analysis.ShipmentRow], list[analysis.NoteRow]]:
    shipments = [
        analysis.ShipmentRow(s["route"], s["route_type"], s["quantity_tonnes"], s["distance_km"],
                             s["freight_cost_inr"], s["shipment_date"])
        for s in parse_shipments(SHIPMENT_FILE.read_bytes(), max_rows=1_000_000)
    ]
    notes = [analysis.NoteRow(n["note_code"], n["note_date"], n["applies_to"], n["text"])
             for n in parse_notes(NOTES_FILE.read_bytes())]
    return shipments, notes


def main() -> None:
    shipments, notes = load_case_study()
    results = analysis.detect(shipments, notes)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=analysis.OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(r.as_output_row() for r in results)

    justified = [r for r in results if not r.needs_review]
    print(f"Total anomalous route-weeks detected: {len(results)}")
    print(f"  Justified anomalies (note found):  {len(justified)}")
    print(f"  Flagged for human review:          {len(results) - len(justified)}")
    for r in results:
        print(f"  {r.route:30s} | {r.week_of} | CPTK={r.cost_per_tonne_km:>5.2f} | {r.flagged:15s} "
              f"| note={r.matched_note_id}")
    print(f"\nOutput written to: {OUTPUT_FILE.name}")

    with open(LOG_FILE, "w") as lf:
        json.dump({
            "model": "None (deterministic rule-based core — no LLM calls)",
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": "$0.00",
            "note": "solution.py is fully deterministic — zero LLM invocations. The Jenius web assistant logs "
                    "every Claude call with its tokens and cost in the llm_calls table.",
        }, lf, indent=2)


if __name__ == "__main__":
    main()
