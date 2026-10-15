"""
FreightTiger SWE Intern (AI) — Case Study Solution  v2
=======================================================
Author  : Jahaan Suthar
Language: Python 3.x  |  stdlib only (no external LLM calls)

Comparison baselines (spec-exact):
  • vs own history    → trailing 8-week rolling average (weeks STRICTLY BEFORE current week)
                        If fewer than 8 prior weeks exist, all available prior weeks are used
                        (documented in README).
  • vs similar routes → average CPTK that same week across all OTHER routes sharing route_type
                        (the route itself is excluded from its own peer average).

Output: output_anomalies.csv matching sample_output_format_v2.csv exactly.
Token log: token_cost_log.json (0 LLM calls for this deterministic core).
"""

import csv
import datetime
import json
from collections import defaultdict
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path("/Users/jahaansuthar/Downloads/FreightTiger/AI Intern Case Study")
SHIPMENT_FILE = BASE_DIR / "shipment_records.csv"
NOTES_FILE    = BASE_DIR / "context_notes.csv"
OUTPUT_FILE   = Path("/Users/jahaansuthar/Downloads/FreightTiger/output_anomalies.csv")
LOG_FILE      = Path("/Users/jahaansuthar/Downloads/FreightTiger/token_cost_log.json")

# Thresholds: flag only when BOTH comparisons exceed the threshold
OWN_HISTORY_THRESHOLD   = 15.0   # percent above trailing-8-week rolling avg
SIMILAR_ROUTE_THRESHOLD = 15.0   # percent above peer-route avg same week
ROLLING_WINDOW_WEEKS    = 8      # spec requirement

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def week_of(date_str: str) -> str:
    """Return the Monday (ISO week start) for a given YYYY-MM-DD date string."""
    d = datetime.date.fromisoformat(date_str)
    monday = d - datetime.timedelta(days=d.weekday())
    return monday.isoformat()


def compute_cptk(row: dict) -> float:
    """cost per tonne per km = freight_cost / (quantity_tonnes * distance_km)."""
    qty  = float(row["quantity_tonnes"])
    dist = float(row["distance_km"])
    cost = float(row["freight_cost_inr"])
    if qty == 0 or dist == 0:
        return 0.0
    return cost / (qty * dist)


def route_label(row: dict) -> str:
    return f"{row['origin']}-{row['destination']}"


# ──────────────────────────────────────────────────────────────────────────────
# NOTE MATCHING
# ──────────────────────────────────────────────────────────────────────────────

def note_applies(note: dict, route: str, week_start: str) -> bool:
    """True if a context note could be relevant to this route and week."""
    note_date  = datetime.date.fromisoformat(note["date"])
    week_date  = datetime.date.fromisoformat(week_start)
    delta_days = abs((note_date - week_date).days)
    if delta_days > 60:
        return False
    applies_to = note["applies_to"].strip()
    if applies_to == "All Routes":
        return True
    applies_norm = applies_to.replace(" ", "-").replace("\u2013", "-")
    route_norm   = route.replace(" ", "-")
    return applies_norm.lower() == route_norm.lower()


def note_explains_cost_rise(note: dict) -> bool:
    """
    Net-score heuristic: does the note primarily describe a genuine cost INCREASE?

    Strategy:
      1. Score positive signals (flooding, surcharge, detour, diesel, etc.)
      2. Score negative/resolution signals (returned to normal, remained stable, etc.)
      3. Return True only if net score > 0.

    This correctly handles:
      - N001: "flooding … detours … higher trip costs … passable again by Mar 9"
        → heavy positives win over the postscript → True ✓
      - N009: "returned to normal after flood repairs"
        → no positives, strong negative → False ✓
      - N006/N008: "remained stable … no major disruptions"
        → net negative → False ✓
    """
    text = note["note"].lower()

    positive = [
        ("flood",                 4),
        ("flooding",              4),
        ("disrupted",             3),
        ("surcharge",             4),
        ("forcing longer detours",4),
        ("higher trip costs",     4),
        ("detour",                2),
        ("diesel",                3),
        ("pushing up",            3),
        ("festival",              3),
        ("limited truck",         2),
        ("maintenance",           1),
        ("delayed",               1),
        ("demand",                1),
        ("rose",                  1),
    ]
    negative = [
        ("returned to normal",                 4),
        ("passable again by",                  3),
        ("no significant disruption",          4),
        ("no major disruption",                4),
        ("remained stable",                    4),
        ("remained normal",                    4),
        ("no rate change",                     4),
        ("not significantly affected",         4),
        ("without a rate change",              4),
        ("absorbed by transporters without",   4),
        ("costs were not significantly affected", 4),
        ("improved road",                      2),
    ]
    pos_score = sum(w for phrase, w in positive if phrase in text)
    neg_score = sum(w for phrase, w in negative if phrase in text)
    return pos_score - neg_score > 0


# ──────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ──────────────────────────────────────────────────────────────────────────────

with open(SHIPMENT_FILE, newline="", encoding="utf-8") as f:
    shipments = list(csv.DictReader(f))

with open(NOTES_FILE, newline="", encoding="utf-8") as f:
    notes = [n for n in csv.DictReader(f) if n.get("note_id")]  # strip blank rows

# Annotate every shipment row
for s in shipments:
    s["_route"] = route_label(s)
    s["_week"]  = week_of(s["shipment_date"])
    s["_cptk"]  = compute_cptk(s)

# ──────────────────────────────────────────────────────────────────────────────
# BUILD WEEKLY AVERAGES PER ROUTE
# ──────────────────────────────────────────────────────────────────────────────

route_week_data: dict[tuple, list] = defaultdict(list)
for s in shipments:
    route_week_data[(s["_route"], s["_week"])].append(s["_cptk"])

weekly_avg: dict[tuple, float] = {
    key: sum(vals) / len(vals)
    for key, vals in route_week_data.items()
}

# Sorted week list per route (needed for rolling window)
route_sorted_weeks: dict[str, list] = defaultdict(list)
for (route, week) in weekly_avg:
    route_sorted_weeks[route].append(week)
for route in route_sorted_weeks:
    route_sorted_weeks[route].sort()

# ──────────────────────────────────────────────────────────────────────────────
# TRAILING 8-WEEK ROLLING AVERAGE (STRICTLY PRIOR WEEKS)
# ──────────────────────────────────────────────────────────────────────────────

def rolling_baseline(route: str, current_week: str) -> tuple[float | None, int]:
    """
    Return (mean, n) where mean is the average of the up-to-8 most recent weekly
    averages STRICTLY BEFORE current_week.  n < 8 is documented per spec.
    Returns (None, 0) if zero prior weeks exist (skip — can't compare).
    """
    prior = sorted(
        (w for w in route_sorted_weeks[route] if w < current_week),
        reverse=True
    )[:ROLLING_WINDOW_WEEKS]
    if not prior:
        return None, 0
    vals = [weekly_avg[(route, w)] for w in prior]
    return sum(vals) / len(vals), len(prior)


# ──────────────────────────────────────────────────────────────────────────────
# PEER-ROUTE AVERAGE (SAME ROUTE_TYPE, EXCLUDING SELF)
# ──────────────────────────────────────────────────────────────────────────────

route_type_map: dict[str, str] = {}
for s in shipments:
    route_type_map[s["_route"]] = s["route_type"]


def peer_avg(route: str, week: str) -> float | None:
    """
    Average weekly CPTK across all routes sharing the same route_type as `route`
    in the given week, EXCLUDING `route` itself.  Returns None if no peers.
    """
    rtype = route_type_map.get(route, "")
    peers = [
        avg
        for (r, w), avg in weekly_avg.items()
        if w == week and r != route and route_type_map.get(r) == rtype
    ]
    return sum(peers) / len(peers) if peers else None


# ──────────────────────────────────────────────────────────────────────────────
# DETECT ANOMALIES
# ──────────────────────────────────────────────────────────────────────────────

anomalies = []
short_window_notes: list[str] = []  # for README transparency

for (route, week), avg_cptk in weekly_avg.items():
    hist_avg, n_hist = rolling_baseline(route, week)
    if hist_avg is None:
        continue  # no prior data → cannot compare

    if n_hist < ROLLING_WINDOW_WEEKS:
        short_window_notes.append(f"{route}@{week}(only {n_hist}w of history)")

    pct_vs_own = (avg_cptk - hist_avg) / hist_avg * 100.0

    p_avg = peer_avg(route, week)
    if p_avg is None or p_avg == 0:
        continue
    pct_vs_sim = (avg_cptk - p_avg) / p_avg * 100.0

    if pct_vs_own >= OWN_HISTORY_THRESHOLD and pct_vs_sim >= SIMILAR_ROUTE_THRESHOLD:
        anomalies.append({
            "route":             route,
            "week_of":           week,
            "cost_per_tonne_km": round(avg_cptk, 2),
            "pct_vs_own":        pct_vs_own,
            "pct_vs_sim":        pct_vs_sim,
            "n_hist":            n_hist,
        })

anomalies.sort(key=lambda x: (x["route"], x["week_of"]))
print(f"Total anomalous route-weeks detected: {len(anomalies)}")
if short_window_notes:
    print(f"\nRoute-weeks with fewer than 8 prior weeks of history ({len(short_window_notes)} cases):")
    for s in short_window_notes[:10]:
        print(f"  {s}")

# ──────────────────────────────────────────────────────────────────────────────
# MATCH NOTES & BUILD OUTPUT ROWS
# ──────────────────────────────────────────────────────────────────────────────

output_rows = []

for a in anomalies:
    route    = a["route"]
    week     = a["week_of"]
    avg_cptk = a["cost_per_tonne_km"]
    pct_own  = a["pct_vs_own"]
    pct_sim  = a["pct_vs_sim"]
    n_hist   = a["n_hist"]

    hist_label = f"trailing {n_hist}-week avg" if n_hist < ROLLING_WINDOW_WEEKS else "trailing 8-week avg"
    own_str = f"{'+' if pct_own >= 0 else ''}{pct_own:.1f}% vs this route's {hist_label}"
    sim_str = f"{'+' if pct_sim >= 0 else ''}{pct_sim:.1f}% vs similar-length routes this week"

    candidates = [n for n in notes if note_applies(n, route, week)]

    matched_note_id = ""
    flagged = "Yes"
    reason  = ""

    if candidates:
        explaining = [n for n in candidates if note_explains_cost_rise(n)]
        if explaining:
            week_date = datetime.date.fromisoformat(week)
            explaining.sort(key=lambda n: abs((datetime.date.fromisoformat(n["date"]) - week_date).days))
            best = explaining[0]
            matched_note_id = best["note_id"]
            flagged = "No (justified)"
            reason = (
                f"Matches note {best['note_id']} dated {best['date']}: "
                f"{best['note'].strip()} The cost rise has a clear explanation."
            )
        else:
            week_date = datetime.date.fromisoformat(week)
            candidates.sort(key=lambda n: abs((datetime.date.fromisoformat(n["date"]) - week_date).days))
            closest = candidates[0]
            flagged = "Yes"
            reason = (
                f"The closest note ({closest['note_id']}, {closest['date']}) "
                f"mentions: \"{closest['note'].strip()}\" -- "
                f"it does not describe a reason for a cost rise on this route. "
                f"No genuine justification found; flagged for review."
            )
    else:
        flagged = "Yes"
        reason  = (
            "No matching note found for this route or date range. "
            "Cost rise looks unexplained and worth a human review."
        )

    output_rows.append({
        "route":             route,
        "week_of":           week,
        "cost_per_tonne_km": f"{avg_cptk:.2f}",
        "vs_own_history":    own_str,
        "vs_similar_routes": sim_str,
        "flagged":           flagged,
        "matched_note_id":   matched_note_id,
        "reason":            reason,
    })

# ──────────────────────────────────────────────────────────────────────────────
# WRITE OUTPUT
# ──────────────────────────────────────────────────────────────────────────────

fieldnames = [
    "route", "week_of", "cost_per_tonne_km",
    "vs_own_history", "vs_similar_routes",
    "flagged", "matched_note_id", "reason"
]

with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(output_rows)

print(f"\nOutput written to: {OUTPUT_FILE}")
print(f"Total rows: {len(output_rows)}")

justified          = [r for r in output_rows if r["flagged"].startswith("No")]
flagged_for_review = [r for r in output_rows if r["flagged"] == "Yes"]

print(f"\nSummary:")
print(f"  Justified anomalies (note found):  {len(justified)}")
print(f"  Flagged for human review:          {len(flagged_for_review)}")

print("\nSample output rows:")
for row in output_rows[:10]:
    print(f"  {row['route']:30s} | {row['week_of']} | CPTK={row['cost_per_tonne_km']:>5} | {row['flagged']:15s} | note={row['matched_note_id']}")

# ──────────────────────────────────────────────────────────────────────────────
# TOKEN / COST LOG
# ──────────────────────────────────────────────────────────────────────────────

cost_log = {
    "model":          "None (deterministic rule-based core — no LLM calls)",
    "llm_calls":      0,
    "input_tokens":   0,
    "output_tokens":  0,
    "estimated_cost_usd": "$0.00",
    "note": (
        "solution.py is fully deterministic — zero LLM invocations. "
        "The conversational AI assistant (assistant.py) uses free-tier embeddings; "
        "its token log is written separately by assistant.py."
    )
}

with open(LOG_FILE, "w") as lf:
    json.dump(cost_log, lf, indent=2)

print(f"\nToken/cost log written to: {LOG_FILE}")
