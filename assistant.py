"""
FreightTiger AI Assistant  (assistant.py)
==========================================
Conversational Q&A interface over the anomaly detection results.

Architecture:
  • RAG vector store: TF-IDF cosine similarity over context_notes.csv
    (no GPU, no API key, fully deterministic at query time)
  • LLM: NONE — answers are built from structured data + retrieved notes
    (zero hallucination risk, zero cost, fully reproducible)
  • Token log: token_cost_log_assistant.json written after each session

Usage:
  python3 assistant.py
  python3 assistant.py --query "why did Chennai-Bangalore get pricier in Feb 2025?"
"""

import argparse
import csv
import datetime
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path("/Users/jahaansuthar/Downloads/FreightTiger/AI Intern Case Study")
OUTPUT_FILE   = Path("/Users/jahaansuthar/Downloads/FreightTiger/output_anomalies.csv")
NOTES_FILE    = BASE_DIR / "context_notes.csv"
TOKEN_LOG     = Path("/Users/jahaansuthar/Downloads/FreightTiger/token_cost_log_assistant.json")

# ──────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ──────────────────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if any(r.values())]

anomalies = load_csv(OUTPUT_FILE)
notes     = load_csv(NOTES_FILE)

# ──────────────────────────────────────────────────────────────────────────────
# TF-IDF VECTOR STORE  (deterministic, no API calls)
# ──────────────────────────────────────────────────────────────────────────────

def tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def build_tfidf(documents: list[str]) -> tuple[list[dict], dict]:
    """Build TF-IDF vectors for each document. Returns (tf_idf_list, idf_map)."""
    n = len(documents)
    df: dict[str, int] = defaultdict(int)
    tf_list: list[dict] = []

    for doc in documents:
        tokens = tokenise(doc)
        freq: dict[str, int] = defaultdict(int)
        for t in tokens:
            freq[t] += 1
        tf: dict[str, float] = {t: c / len(tokens) for t, c in freq.items()}
        tf_list.append(tf)
        for t in tf:
            df[t] += 1

    idf: dict[str, float] = {
        t: math.log((n + 1) / (cnt + 1)) + 1   # smooth IDF
        for t, cnt in df.items()
    }

    tfidf_list = []
    for tf in tf_list:
        vec = {t: tf[t] * idf.get(t, 0) for t in tf}
        # L2-normalise
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1
        tfidf_list.append({t: v / norm for t, v in vec.items()})

    return tfidf_list, idf


def cosine(a: dict, b: dict) -> float:
    return sum(a.get(t, 0) * v for t, v in b.items())


# Build vector store over notes
note_docs   = [f"{n['applies_to']} {n['note']}" for n in notes]
note_vecs, idf_map = build_tfidf(note_docs)


def retrieve_notes(query: str, top_k: int = 3) -> list[tuple[dict, float]]:
    """Return top_k notes most similar to query (TF-IDF cosine)."""
    tokens = tokenise(query)
    freq: dict[str, int] = defaultdict(int)
    for t in tokens:
        freq[t] += 1
    tf = {t: c / len(tokens) for t, c in freq.items()}
    q_vec = {t: tf[t] * idf_map.get(t, 0) for t in tf}
    norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1
    q_vec = {t: v / norm for t, v in q_vec.items()}

    scored = [(notes[i], cosine(q_vec, note_vecs[i])) for i in range(len(notes))]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(n, s) for n, s in scored[:top_k] if s > 0]

# ──────────────────────────────────────────────────────────────────────────────
# ANSWER ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def route_anomalies(route_query: str) -> list[dict]:
    """Return anomaly rows matching the route query (case-insensitive partial match)."""
    q = route_query.lower().replace(" ", "-")
    return [a for a in anomalies if q in a["route"].lower().replace(" ", "-")]


def date_in_range(week_str: str, month_hint: str | None, year_hint: str | None) -> bool:
    try:
        week_date = datetime.date.fromisoformat(week_str)
        if month_hint:
            month_names = {
                "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
            }
            m = month_names.get(month_hint[:3].lower())
            if m and week_date.month != m:
                return False
        if year_hint:
            if week_date.year != int(year_hint):
                return False
        return True
    except Exception:
        return True


def answer(query: str) -> str:
    """
    Build a grounded answer from structured anomaly data + retrieved notes.
    No LLM is used — every sentence is sourced from actual data.
    """
    q_lower = query.lower()

    # Extract temporal hints
    month_map = {
        "january":"jan","february":"feb","march":"mar","april":"apr",
        "may":"may","june":"jun","july":"jul","august":"aug",
        "september":"sep","october":"oct","november":"nov","december":"dec",
        "jan":"jan","feb":"feb","mar":"mar","apr":"apr",
        "jun":"jun","jul":"jul","aug":"aug","sep":"sep","oct":"oct","nov":"nov","dec":"dec"
    }
    month_hint = next((v for k, v in month_map.items() if k in q_lower), None)
    year_match = re.search(r"\b(202\d)\b", query)
    year_hint  = year_match.group(1) if year_match else None

    # Extract route hint
    route_hint = None
    for a in anomalies:
        parts = a["route"].lower().split("-")
        if any(p in q_lower for p in parts):
            route_hint = a["route"]
            break

    # Build relevant anomaly subset
    if route_hint:
        relevant = [a for a in anomalies
                    if a["route"].lower() == route_hint.lower()
                    and date_in_range(a["week_of"], month_hint, year_hint)]
    elif month_hint or year_hint:
        relevant = [a for a in anomalies if date_in_range(a["week_of"], month_hint, year_hint)]
    else:
        relevant = list(anomalies)

    # ── Summaries ──
    if "how many" in q_lower or "summary" in q_lower or "overview" in q_lower:
        total     = len(anomalies)
        justified = sum(1 for a in anomalies if a["flagged"].startswith("No"))
        flagged   = total - justified
        routes    = sorted({a["route"] for a in anomalies})
        lines = [
            f"**Anomaly Summary**",
            f"- Total anomalous route-weeks detected: **{total}**",
            f"- Justified by a context note: **{justified}**",
            f"- Flagged for human review (unexplained): **{flagged}**",
            f"- Routes affected: {', '.join(routes)}",
        ]
        return "\n".join(lines)

    if not relevant:
        # Fall back to RAG-retrieved notes
        hits = retrieve_notes(query, top_k=2)
        if hits:
            lines = ["I couldn't find a matching anomaly for your query, but here are the most relevant context notes:"]
            for n, score in hits:
                lines.append(f"\n**{n['note_id']}** ({n['date']}, {n['applies_to']})\n> {n['note']}")
            return "\n".join(lines)
        return "I don't have enough data to answer that question. Try asking about a specific route like 'Chennai-Bangalore' or a time period like '2025'."

    # Build per-row answer
    lines = []
    if route_hint and len(relevant) == 1:
        a = relevant[0]
        lines.append(f"**Route:** {a['route']}  |  **Week of:** {a['week_of']}")
        lines.append(f"**Cost per tonne-km:** ₹{a['cost_per_tonne_km']}")
        lines.append(f"**vs own history:** {a['vs_own_history']}")
        lines.append(f"**vs similar routes:** {a['vs_similar_routes']}")
        lines.append(f"**Verdict:** {a['flagged']}")
        if a['matched_note_id']:
            note = next((n for n in notes if n['note_id'] == a['matched_note_id']), None)
            if note:
                lines.append(f"\n**Supporting evidence (Note {note['note_id']}, {note['date']}):**")
                lines.append(f"> {note['note']}")
        lines.append(f"\n**Full explanation:** {a['reason']}")
    else:
        lines.append(f"Found **{len(relevant)}** anomalous route-week(s) matching your query:\n")
        for a in relevant:
            verdict = "✅ Justified" if a['flagged'].startswith("No") else "⚠️ Unexplained"
            note_tag = f" [Note {a['matched_note_id']}]" if a['matched_note_id'] else ""
            lines.append(
                f"- **{a['route']}** week of {a['week_of']}: "
                f"CPTK=₹{a['cost_per_tonne_km']}, "
                f"{a['vs_own_history']}, {a['vs_similar_routes']} "
                f"→ {verdict}{note_tag}"
            )
        # Attach retrieved notes
        hits = retrieve_notes(query, top_k=2)
        if hits:
            lines.append("\n**Relevant context notes:**")
            for n, _ in hits:
                lines.append(f"- **{n['note_id']}** ({n['date']}, {n['applies_to']}): {n['note']}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# TOKEN / COST LOG
# ──────────────────────────────────────────────────────────────────────────────

_query_count = 0

def log_query():
    global _query_count
    _query_count += 1
    log = {
        "model": "None (TF-IDF retrieval + structured data — no LLM)",
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": "$0.00",
        "session_queries": _query_count,
        "note": (
            "The assistant uses TF-IDF cosine similarity for note retrieval and "
            "structured data for answer generation. Zero LLM API calls are made. "
            "This ensures full reproducibility and zero cost."
        )
    }
    with open(TOKEN_LOG, "w") as f:
        json.dump(log, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# CLI INTERFACE
# ──────────────────────────────────────────────────────────────────────────────

WELCOME = """
╔══════════════════════════════════════════════════════════════╗
║      FreightTiger AI Assistant — Shipping Cost Monitor       ║
║  Ask me about anomalous route costs, justifications, notes   ║
║  Type 'exit' or Ctrl+C to quit.                              ║
╚══════════════════════════════════════════════════════════════╝

Loaded {n_anomalies} anomalous route-weeks | {n_notes} context notes

Example questions:
  • "Why did Chennai-Bangalore get pricier in February 2025?"
  • "Which routes are flagged for human review?"
  • "Give me a summary of all anomalies"
  • "Was the Ahmedabad-Mumbai cost spike justified?"
  • "What happened in March 2025?"
"""

def main():
    parser = argparse.ArgumentParser(description="FreightTiger AI Assistant")
    parser.add_argument("--query", "-q", type=str, help="Single query (non-interactive)")
    args = parser.parse_args()

    if args.query:
        print(answer(args.query))
        log_query()
        return

    print(WELCOME.format(n_anomalies=len(anomalies), n_notes=len(notes)))

    while True:
        try:
            q = input("\n🔍 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "bye"):
            print("Goodbye!")
            break
        resp = answer(q)
        log_query()
        print("\n🤖 Assistant:\n" + resp)


if __name__ == "__main__":
    main()
