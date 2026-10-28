"""
FreightTiger AI Assistant  (assistant.py)
==========================================
Terminal Q&A over the case-study anomalies, with the free offline engine (no LLM, no database).
The web app adds Claude on top of the same data: see README.md.

Usage:
  python3 assistant.py
  python3 assistant.py --query "why did Chennai-Bangalore get pricier in Feb 2025?"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jenius import analysis  # noqa: E402
from jenius.assistant import offline  # noqa: E402
from jenius.models import Anomaly, ContextNote  # noqa: E402
from solution import load_case_study  # noqa: E402

WELCOME = """
╔══════════════════════════════════════════════════════════════╗
║      Jenius AI Assistant — Shipping Cost Monitor (offline)   ║
║  Ask me about anomalous route costs, justifications, notes   ║
║  Type 'exit' or Ctrl+C to quit.                              ║
╚══════════════════════════════════════════════════════════════╝

Loaded {n_anomalies} anomalous route-weeks | {n_notes} context notes

Example questions:
  • "Why did Chennai-Bangalore get pricier in February 2025?"
  • "Which routes are flagged for human review?"
  • "Give me a summary of all anomalies"
  • "What happened in March 2025?"
"""


def load():
    shipments, notes = load_case_study()
    results = analysis.detect(shipments, notes)
    anomalies = [
        Anomaly(route=r.route, week_of=r.week_of, cost_per_tonne_km=r.cost_per_tonne_km, pct_vs_own=r.pct_vs_own,
                pct_vs_similar=r.pct_vs_similar, history_weeks=r.history_weeks, vs_own_history=r.vs_own_history,
                vs_similar_routes=r.vs_similar_routes, needs_review=r.needs_review,
                matched_note_code=r.matched_note_id, reason=r.reason)
        for r in results
    ]
    note_rows = [ContextNote(note_code=n.note_id, note_date=n.date, applies_to=n.applies_to, text=n.note)
                 for n in notes]
    routes = sorted({s.route for s in shipments})
    return anomalies, note_rows, routes


def main() -> None:
    parser = argparse.ArgumentParser(description="Jenius offline assistant")
    parser.add_argument("--query", "-q", type=str, help="Single query (non-interactive)")
    args = parser.parse_args()
    anomalies, notes, routes = load()

    if args.query:
        print(offline.answer(args.query, anomalies, notes, routes))
        return

    print(WELCOME.format(n_anomalies=len(anomalies), n_notes=len(notes)))
    while True:
        try:
            q = input("\n🔍 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if q.lower() in ("exit", "quit", "bye"):
            print("Goodbye!")
            break
        if q:
            print("\n🤖 Assistant:\n" + offline.answer(q, anomalies, notes, routes))


if __name__ == "__main__":
    main()
