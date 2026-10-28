"""The free, offline answer engine (from the original assistant.py).

No LLM: answers are built from the stored anomalies and the TF-IDF note search, so every sentence
comes straight from the data. Used for visitors who aren't signed in, when no Claude key is
configured, and as the fallback when Claude can't be reached.
"""
import datetime as dt
import re
from collections.abc import Sequence

from ..analysis import week_start
from ..models import Anomaly, ContextNote
from ..retrieval import NoteIndex

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b")
_SUMMARY_RE = re.compile(r"\b(how many|summary|summarise|summarize|overview|total)\b")


def _verdict(a: Anomaly) -> str:
    return "⚠️ Needs review" if a.needs_review else "✅ Justified"


def _find_route(question: str, routes: Sequence[str]) -> str | None:
    """A route is mentioned when all its city names appear; otherwise the first route sharing a city."""
    q = question.lower()
    words = set(re.findall(r"[a-z]+", q))
    partial = None
    for route in sorted(routes):
        cities = [c.strip().lower() for c in route.split("-")]
        hits = [c for c in cities if c and (c in words or c in q)]
        if len(hits) == len(cities):
            return route
        if hits and partial is None:
            partial = route
    return partial


_CARRIER_RE = re.compile(r"\b(carrier|carriers|transporter|transporters|truckers?)\b")
_COMPARE_RE = re.compile(r"\b(compare|comparison|cheapest|costliest|most expensive|across all routes|every route)\b")


def _carrier_table(toolbox, route: str | None) -> str:
    rows = toolbox.tool_transporter_breakdown(route=route)["transporters"]
    lines = [f"**Transporters by average cost per tonne-km** ({route or 'all routes'}):\n",
             "| Transporter | Shipments | Spend | ₹/tonne-km |", "|---|---|---|---|"]
    lines += [f"| {r['transporter']} | {r['shipments']:,} | ₹{r['freight_cost_inr']:,.0f} | "
              f"{r['avg_cost_per_tonne_km']:.3f} |" for r in rows]
    return "\n".join(lines)


def _route_table(toolbox) -> str:
    rows = toolbox.tool_compare_routes()["routes"]
    lines = ["**Routes by average cost per tonne-km:**\n",
             "| Route | Type | Shipments | Spend | ₹/tonne-km |", "|---|---|---|---|---|"]
    lines += [f"| {r['route']} | {r['route_type']} | {r['shipments']:,} | ₹{r['freight_cost_inr']:,.0f} | "
              f"{r['avg_cost_per_tonne_km']:.3f} |"
              for r in sorted(rows, key=lambda r: r["avg_cost_per_tonne_km"], reverse=True)]
    return "\n".join(lines)


def answer(question: str, anomalies: Sequence[Anomaly], notes: Sequence[ContextNote], routes: Sequence[str],
           toolbox=None) -> str:
    """`toolbox` (a ToolBox for the same dataset) adds answers about carriers and route comparisons."""
    q = question.lower()
    if toolbox is not None and _CARRIER_RE.search(q):
        return _carrier_table(toolbox, _find_route(q, routes) if re.search(r"\bon\b|\bfor\b", q) else None)
    if toolbox is not None and _COMPARE_RE.search(q):
        return _route_table(toolbox)
    iso = re.search(r"\b(20\d\d-\d\d-\d\d)\b", q)
    try:
        week = week_start(dt.date.fromisoformat(iso.group(1))) if iso else None
    except ValueError:
        week = None
    month_match = _MONTH_RE.search(q)
    month = MONTHS[month_match.group(1)] if month_match else None
    year_match = re.search(r"\b(20\d\d)\b", q)
    year = int(year_match.group(1)) if year_match else None

    if _SUMMARY_RE.search(q) and not week and not month and not year and not _find_route(q, routes):
        justified = sum(1 for a in anomalies if not a.needs_review)
        lines = [
            "**Anomaly summary**",
            f"- Anomalous route-weeks detected: **{len(anomalies)}**",
            f"- Justified by a context note: **{justified}**",
            f"- Needing human review: **{len(anomalies) - justified}**",
        ]
        if anomalies:
            lines.append(f"- Routes affected: {', '.join(sorted({a.route for a in anomalies}))}")
        return "\n".join(lines)

    route = _find_route(q, routes)
    wants_review = bool(re.search(r"\b(flag|flagged|review|unexplained|suspicious)\b", q))

    relevant = [
        a for a in anomalies
        if (route is None or a.route == route)
        and (week is None or a.week_of == week)
        and (month is None or a.week_of.month == month)
        and (year is None or a.week_of.year == year)
        and (not wants_review or a.needs_review)
    ]
    index = NoteIndex(notes)

    if not relevant:
        hits = index.search(question, top_k=2)
        scope = f" for {route}" if route else ""
        if hits:
            lines = [f"I didn't find an anomaly{scope} matching that. The closest context notes are:"]
            lines += [f"\n**{n.note_code}** ({n.note_date}, {n.applies_to})\n> {n.text}" for n, _ in hits]
            return "\n".join(lines)
        return (f"I didn't find an anomaly{scope} matching that. Try a route such as "
                f"'{sorted(routes)[0] if routes else 'Chennai-Bangalore'}', a month, or 'summary'.")

    if len(relevant) == 1:
        a = relevant[0]
        lines = [
            f"**{a.route}**, week of {a.week_of}",
            f"- Cost per tonne-km: ₹{a.cost_per_tonne_km:.2f}",
            f"- vs own history: {a.vs_own_history}",
            f"- vs similar routes: {a.vs_similar_routes}",
            f"- Verdict: {_verdict(a)}",
        ]
        note = next((n for n in notes if n.note_code == a.matched_note_code), None) if a.matched_note_code else None
        if note:
            lines += [f"\n**Supporting evidence, note {note.note_code} ({note.note_date}):**", f"> {note.text}"]
        lines.append(f"\n**Engine's reasoning:** {a.reason}")
        return "\n".join(lines)

    lines = [f"Found **{len(relevant)}** anomalous route-weeks:\n",
             "| Route | Week of | ₹/tonne-km | vs own | vs peers | Verdict | Note |",
             "|---|---|---|---|---|---|---|"]
    for a in relevant:
        lines.append(f"| {a.route} | {a.week_of} | {a.cost_per_tonne_km:.2f} | {a.pct_vs_own:+.1f}% | "
                     f"{a.pct_vs_similar:+.1f}% | {_verdict(a)} | {a.matched_note_code or '—'} |")
    hits = index.search(question, top_k=2)
    if hits:
        lines.append("\n**Relevant context notes:**")
        lines += [f"- **{n.note_code}** ({n.note_date}, {n.applies_to}): {n.text}" for n, _ in hits]
    return "\n".join(lines)
