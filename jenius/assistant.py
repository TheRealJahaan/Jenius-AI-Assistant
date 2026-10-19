"""Grounded question answering over the anomaly knowledge base.

No language model is involved: questions are parsed for routes, periods, note
ids and intent, and answers are assembled from pre-formatted facts in the
knowledge base, so every number and quote traces back to the input files.
Free-text questions that name nothing concrete fall back to TF-IDF retrieval
over the context notes.

``web/assistant.js`` is a line-for-line port of this module used by the static
site; ``tests/test_parity.py`` asserts both produce identical answers.
"""

from __future__ import annotations

import math
import re
from typing import Any

KB = dict[str, Any]
Row = dict[str, Any]

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
              "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}  # fmt: skip
CITY_ALIASES = {"bengaluru": "bangalore", "bombay": "mumbai", "madras": "chennai",
                "calcutta": "kolkata"}  # fmt: skip
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from get got how in is it of on or "  # noqa: SIM905
    "the this that to was were what when where which why with".split()
)

HELP = "\n".join(
    [
        "I can answer questions about freight cost anomalies in this dataset. Try:",
        "- *Give me a summary of all anomalies*",
        "- *Why did Chennai-Bangalore get pricier in February 2025?*",
        "- *Which weeks need human review?*",
        "- *Which route is the most expensive?*",
        "- *What does note N009 say?*",
        "- *How did Mumbai-Pune do in 2025?*",
    ]
)


def tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def group_digits(n: int) -> str:
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ",", str(n))


def month_label(iso: str) -> str:
    return f"{MONTHS[int(iso[5:7]) - 1].capitalize()[:3]} {iso[:4]}"


class Assistant:
    def __init__(self, kb: KB) -> None:
        self.kb = kb
        self.meta: dict[str, Any] = kb["meta"]
        self.routes: list[Row] = kb["routes"]
        self.route_weeks: list[Row] = kb["route_weeks"]
        self.notes: list[Row] = kb["notes"]
        self.anomalies = [rw for rw in self.route_weeks if rw["anomalous"]]
        self._build_index()

    # ── retrieval ────────────────────────────────────────────────────────────
    def _vector(self, tokens: list[str]) -> dict[str, float]:
        tokens = [t for t in tokens if t not in STOPWORDS]
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        vec = {t: c / len(tokens) * self.idf.get(t, 0.0) for t, c in counts.items()}
        # Plain left-to-right sums (not sum(), which compensates since 3.12) keep
        # the arithmetic bit-identical to the JavaScript port.
        sq = 0.0
        for v in vec.values():
            sq += v * v
        norm = math.sqrt(sq) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def _build_index(self) -> None:
        docs = [tokenise(f"{n['applies_to']} {n['note']}") for n in self.notes]
        df: dict[str, int] = {}
        for doc in docs:
            for t in set(doc) - STOPWORDS:
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.note_vecs = [self._vector(doc) for doc in docs]

    def retrieve(self, query: str, k: int = 2) -> list[Row]:
        q = self._vector(tokenise(query))
        scored = []
        for note, vec in zip(self.notes, self.note_vecs, strict=True):
            score = 0.0
            for t, v in q.items():
                score += v * vec.get(t, 0.0)
            if score > 0:
                scored.append((score, note))
        scored.sort(key=lambda x: -x[0])
        return [note for _, note in scored[:k]]

    # ── parsing ──────────────────────────────────────────────────────────────
    @staticmethod
    def parse_period(tokens: list[str]) -> tuple[int | None, str | None]:
        month = None
        for i, t in enumerate(tokens):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            prev = tokens[i - 1] if i > 0 else ""
            if t in MONTHS and t != "may":
                month = MONTHS.index(t) + 1
            elif t in MONTH_ABBR:
                month = MONTH_ABBR[t]
            elif t == "may" and (re.fullmatch(r"20\d\d", nxt) or prev in ("in", "of")):
                month = 5
            if month:
                break
        year = next((t for t in tokens if re.fullmatch(r"20\d\d", t)), None)
        return month, year

    def parse_routes(self, text: str) -> list[str]:
        words = set(re.findall(r"[a-z]+", text))
        words |= {CITY_ALIASES[w] for w in words if w in CITY_ALIASES}
        best, hits = 0, []
        for r in self.routes:
            score = sum(1 for city in r["route"].lower().split("-") if city in words)
            if score > best:
                best, hits = score, [r["route"]]
            elif score == best and score > 0:
                hits.append(r["route"])
        return hits

    @staticmethod
    def in_period(week_of: str, month: int | None, year: str | None) -> bool:
        if year and week_of[:4] != year:
            return False
        return not (month and int(week_of[5:7]) != month)

    @staticmethod
    def period_label(month: int | None, year: str | None) -> str:
        if month and year:
            return f" in {MONTHS[month - 1].capitalize()} {year}"
        if month:
            return f" in {MONTHS[month - 1].capitalize()}"
        return f" in {year}" if year else ""

    # ── formatting ───────────────────────────────────────────────────────────
    def note_by_id(self, note_id: str) -> Row | None:
        return next((n for n in self.notes if n["note_id"] == note_id), None)

    @staticmethod
    def verdict(rw: Row) -> str:
        if rw["matched_note_id"]:
            return f"✅ Justified by {rw['matched_note_id']}"
        return "⚠️ Needs human review"

    def bullet(self, rw: Row) -> str:
        return (
            f"- **{rw['route']}**, week of {rw['week_of']}: ₹{rw['cptk_label']}/t-km, "
            f"{rw['own_label']} vs own history, {rw['peer_label']} vs peers. {self.verdict(rw)}"
        )

    def detail(self, rw: Row) -> str:
        lines = [
            f"**{rw['route']}, week of {rw['week_of']}**",
            f"- Cost: ₹{rw['cptk_label']} per tonne-km",
            f"- vs own history: {rw['vs_own_history']}",
            f"- vs similar routes: {rw['vs_similar_routes']}",
            f"- Verdict: {self.verdict(rw)}",
            f"- What drove it: {rw['drivers']}",
        ]
        note = self.note_by_id(rw["matched_note_id"]) if rw["matched_note_id"] else None
        if note:
            lines += ["", f"> **{note['note_id']}** ({note['date']}): {note['note']}"]
        else:
            lines += ["", rw["reason"]]
        return "\n".join(lines)

    def anomaly_list(self, rows: list[Row], heading: str) -> str:
        lines = [heading, ""] + [self.bullet(rw) for rw in rows]
        cited = []
        for rw in rows:
            if rw["matched_note_id"] and rw["matched_note_id"] not in cited:
                cited.append(rw["matched_note_id"])
        if cited:
            lines += ["", "**Evidence**"]
            for nid in cited:
                note = self.note_by_id(nid)
                if note:
                    lines.append(f"> **{nid}** ({note['date']}): {note['note']}")
        return "\n".join(lines)

    # ── intents ──────────────────────────────────────────────────────────────
    def summary(self) -> str:
        m = self.meta
        justified = [a for a in self.anomalies if a["matched_note_id"]]
        routes = sorted({a["route"] for a in self.anomalies})
        return "\n".join(
            [
                f"**Anomaly summary** ({month_label(m['date_from'])} to {month_label(m['date_to'])}, "
                f"{group_digits(m['n_shipments'])} shipments across {m['n_routes']} routes)",
                f"- Anomalous route-weeks: **{len(self.anomalies)}**",
                f"- Justified by a context note: **{len(justified)}**",
                f"- Needs human review: **{len(self.anomalies) - len(justified)}**",
                f"- Routes affected: {', '.join(routes) if routes else 'none'}",
                "",
                f"A week is flagged when its cost per tonne-km is at least "
                f"{m['own_history_threshold_pct']:g}% above the route's trailing "
                f"{m['rolling_window_weeks']}-week average **and** at least "
                f"{m['peer_threshold_pct']:g}% above similar routes that same week.",
            ]
        )

    def ranking(self, routes: list[str], cheapest: bool) -> str:
        pool = [r for r in self.routes if not routes or r["route"] in routes]
        pool = sorted(pool, key=lambda r: r["avg_cptk"], reverse=not cheapest)
        title = "cheapest" if cheapest else "most expensive"
        lines = [f"**Routes ranked {title} first** (average cost per tonne-km, all weeks)", ""]
        for i, r in enumerate(pool, start=1):
            n = r["n_anomalies"]
            lines.append(
                f"{i}. **{r['route']}** ({r['route_type']}): ₹{r['avg_cptk_label']}/t-km, "
                f"{n} anomalous week{'' if n == 1 else 's'}"
            )
        lines += ["", "Distance bands differ, so compare routes within the same type."]
        return "\n".join(lines)

    def note_answer(self, ids: list[str]) -> str:
        blocks = []
        for nid in ids:
            note = self.note_by_id(nid)
            if not note:
                blocks.append(f"There is no note {nid} in the dataset.")
                continue
            sig = note["signals"]
            pos = ", ".join(f"+{p}" for p in sig["positive"]) or "none"
            neg = ", ".join(f"-{p}" for p in sig["negative"]) or "none"
            stance = "a cost driver" if sig["net"] > 0 else "not a cost driver"
            weeks = [a["week_of"] for a in self.anomalies if a["matched_note_id"] == nid]
            lines = [
                f"**{nid}** ({note['date']}, applies to {note['applies_to']})",
                f"> {note['note']}",
                "",
                f"Signals: {pos}; {neg} (net {sig['net']:+d}), so it reads as {stance}.",
            ]
            if weeks:
                lines.append(f"It explains the flagged weeks of {', '.join(weeks)}.")
            else:
                lines.append("It does not explain any flagged week.")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def route_answer(self, routes: list[str], month: int | None, year: str | None) -> str:
        period = self.period_label(month, year)
        hits = [
            a for a in self.anomalies if a["route"] in routes and self.in_period(a["week_of"], month, year)
        ]
        if len(hits) == 1:
            return self.detail(hits[0])
        if hits:
            label = routes[0] if len(routes) == 1 else ", ".join(routes)
            return self.anomaly_list(hits, f"Found **{len(hits)}** anomalous weeks on {label}{period}:")

        blocks = []
        for name in routes:
            r = next(x for x in self.routes if x["route"] == name)
            if not (month or year):
                blocks.append(
                    f"**{name}** had no anomalous weeks. Across {r['n_weeks']} weeks it averaged "
                    f"₹{r['avg_cptk_label']}/t-km, peaking at ₹{r['peak_cptk_label']} in the week of "
                    f"{r['peak_week']}."
                )
                continue
            weeks = [
                w
                for w in self.route_weeks
                if w["route"] == name and self.in_period(w["week_of"], month, year)
            ]
            if not weeks:
                blocks.append(f"**{name}** has no shipments{period}.")
                continue
            lines = [f"**{name}** had no anomalous weeks{period}. Weekly cost per tonne-km:"]
            for w in weeks[:8]:
                extra = f" ({w['own_label']} vs own history)" if w.get("own_label") else ""
                lines.append(f"- {w['week_of']}: ₹{w['cptk_label']}{extra}")
            if len(weeks) > 8:
                lines.append(f"- ...and {len(weeks) - 8} more weeks")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def answer(self, query: str) -> str:
        q = query.lower().strip()
        tokens = re.findall(r"[a-z]+|\d+", q)
        words = set(tokens)
        if not q:
            return HELP

        note_ids = [f"N{int(d):03d}" for d in re.findall(r"\bn0*(\d{1,3})\b", q)]
        if note_ids:
            return self.note_answer(list(dict.fromkeys(note_ids)))

        if words & {"help", "hello", "hi", "hey"} or "what can you" in q:
            return HELP

        month, year = self.parse_period(tokens)
        routes = self.parse_routes(q)

        if (words & {"summary", "overview", "total"} or "how many" in q) and not routes:
            return self.summary()

        if words & {"cheapest", "lowest", "cheaper"}:
            return self.ranking(routes, cheapest=True)
        if words & {"expensive", "highest", "costliest", "rank", "ranking", "compare"}:
            return self.ranking(routes, cheapest=False)

        pool = [
            a for a in self.anomalies
            if (not routes or a["route"] in routes) and self.in_period(a["week_of"], month, year)
        ]  # fmt: skip
        period = self.period_label(month, year)
        if words & {"unexplained", "review", "suspicious", "investigate", "unjustified", "flagged"}:
            rows = [a for a in pool if not a["matched_note_id"]]
            if not rows:
                return f"No unexplained anomalies{period}. Every flagged week has a supporting note."
            if len(rows) == 1 and routes:
                return self.detail(rows[0])
            return self.anomaly_list(rows, f"**{len(rows)}** week(s) need human review{period}:")
        if words & {"justified", "explained", "explains", "legitimate"}:
            rows = [a for a in pool if a["matched_note_id"]]
            if not rows:
                return f"No justified anomalies{period}."
            if len(rows) == 1 and routes:
                return self.detail(rows[0])
            return self.anomaly_list(
                rows, f"**{len(rows)}** anomalous week(s) are explained by a note{period}:"
            )

        if routes:
            return self.route_answer(routes, month, year)
        if month or year:
            if not pool:
                return f"No anomalous weeks{period}."
            return self.anomaly_list(pool, f"Found **{len(pool)}** anomalous week(s){period}:")

        hits = self.retrieve(query)
        if hits:
            lines = ["I couldn't tie that to a route or period, but these notes look relevant:", ""]
            lines += [f"> **{n['note_id']}** ({n['date']}, {n['applies_to']}): {n['note']}" for n in hits]
            return "\n".join(lines)
        return HELP
