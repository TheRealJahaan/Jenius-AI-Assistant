// Grounded question answering over the anomaly knowledge base.
//
// A line-for-line port of jenius/assistant.py so the static site can answer
// questions with no server. tests/test_parity.py runs both implementations on
// the same questions and requires identical output, so change them together.

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];
const MONTH_ABBR = {
  jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8,
  sep: 9, sept: 9, oct: 10, nov: 11, dec: 12,
};
const CITY_ALIASES = { bengaluru: "bangalore", bombay: "mumbai", madras: "chennai", calcutta: "kolkata" };
const STOPWORDS = new Set(
  ("a an and are as at be by did do does for from get got how in is it of on or " +
    "the this that to was were what when where which why with").split(" "),
);

export const HELP = [
  "I can answer questions about freight cost anomalies in this dataset. Try:",
  "- *Give me a summary of all anomalies*",
  "- *Why did Chennai-Bangalore get pricier in February 2025?*",
  "- *Which weeks need human review?*",
  "- *Which route is the most expensive?*",
  "- *What does note N009 say?*",
  "- *How did Mumbai-Pune do in 2025?*",
].join("\n");

const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const intersects = (set, list) => list.some((w) => set.has(w));

export function tokenise(text) {
  return text.toLowerCase().match(/[a-z]+/g) || [];
}

export function groupDigits(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function monthLabel(iso) {
  return `${capitalize(MONTHS[Number(iso.slice(5, 7)) - 1]).slice(0, 3)} ${iso.slice(0, 4)}`;
}

export class Assistant {
  constructor(kb) {
    this.kb = kb;
    this.meta = kb.meta;
    this.routes = kb.routes;
    this.routeWeeks = kb.route_weeks;
    this.notes = kb.notes;
    this.anomalies = this.routeWeeks.filter((rw) => rw.anomalous);
    this.buildIndex();
  }

  // ── retrieval ──────────────────────────────────────────────────────────────
  vector(tokens) {
    tokens = tokens.filter((t) => !STOPWORDS.has(t));
    const counts = new Map();
    for (const t of tokens) counts.set(t, (counts.get(t) || 0) + 1);
    const vec = new Map();
    for (const [t, c] of counts) vec.set(t, (c / tokens.length) * (this.idf.get(t) || 0));
    let sq = 0;
    for (const v of vec.values()) sq += v * v;
    const norm = Math.sqrt(sq) || 1;
    const out = new Map();
    for (const [t, v] of vec) out.set(t, v / norm);
    return out;
  }

  buildIndex() {
    const docs = this.notes.map((n) => tokenise(`${n.applies_to} ${n.note}`));
    const df = new Map();
    for (const doc of docs) {
      for (const t of new Set(doc)) {
        if (!STOPWORDS.has(t)) df.set(t, (df.get(t) || 0) + 1);
      }
    }
    const n = docs.length;
    this.idf = new Map();
    for (const [t, c] of df) this.idf.set(t, Math.log((n + 1) / (c + 1)) + 1);
    this.noteVecs = docs.map((doc) => this.vector(doc));
  }

  retrieve(query, k = 2) {
    const q = this.vector(tokenise(query));
    const scored = [];
    this.notes.forEach((note, i) => {
      const vec = this.noteVecs[i];
      let score = 0;
      for (const [t, v] of q) score += v * (vec.get(t) || 0);
      if (score > 0) scored.push([score, note]);
    });
    scored.sort((a, b) => b[0] - a[0]);
    return scored.slice(0, k).map(([, note]) => note);
  }

  // ── parsing ────────────────────────────────────────────────────────────────
  static parsePeriod(tokens) {
    let month = null;
    for (let i = 0; i < tokens.length; i++) {
      const t = tokens[i];
      const nxt = i + 1 < tokens.length ? tokens[i + 1] : "";
      const prev = i > 0 ? tokens[i - 1] : "";
      if (MONTHS.includes(t) && t !== "may") month = MONTHS.indexOf(t) + 1;
      else if (own(MONTH_ABBR, t)) month = MONTH_ABBR[t];
      else if (t === "may" && (/^20\d\d$/.test(nxt) || prev === "in" || prev === "of")) month = 5;
      if (month) break;
    }
    const year = tokens.find((t) => /^20\d\d$/.test(t)) || null;
    return [month, year];
  }

  parseRoutes(text) {
    const words = new Set(text.match(/[a-z]+/g) || []);
    for (const w of [...words]) if (own(CITY_ALIASES, w)) words.add(CITY_ALIASES[w]);
    let best = 0;
    let hits = [];
    for (const r of this.routes) {
      const score = r.route.toLowerCase().split("-").filter((city) => words.has(city)).length;
      if (score > best) {
        best = score;
        hits = [r.route];
      } else if (score === best && score > 0) {
        hits.push(r.route);
      }
    }
    return hits;
  }

  static inPeriod(weekOf, month, year) {
    if (year && weekOf.slice(0, 4) !== year) return false;
    return !(month && Number(weekOf.slice(5, 7)) !== month);
  }

  static periodLabel(month, year) {
    if (month && year) return ` in ${capitalize(MONTHS[month - 1])} ${year}`;
    if (month) return ` in ${capitalize(MONTHS[month - 1])}`;
    return year ? ` in ${year}` : "";
  }

  // ── formatting ─────────────────────────────────────────────────────────────
  noteById(noteId) {
    return this.notes.find((n) => n.note_id === noteId) || null;
  }

  static verdict(rw) {
    return rw.matched_note_id ? `✅ Justified by ${rw.matched_note_id}` : "⚠️ Needs human review";
  }

  bullet(rw) {
    return (
      `- **${rw.route}**, week of ${rw.week_of}: ₹${rw.cptk_label}/t-km, ` +
      `${rw.own_label} vs own history, ${rw.peer_label} vs peers. ${Assistant.verdict(rw)}`
    );
  }

  detail(rw) {
    const lines = [
      `**${rw.route}, week of ${rw.week_of}**`,
      `- Cost: ₹${rw.cptk_label} per tonne-km`,
      `- vs own history: ${rw.vs_own_history}`,
      `- vs similar routes: ${rw.vs_similar_routes}`,
      `- Verdict: ${Assistant.verdict(rw)}`,
      `- What drove it: ${rw.drivers}`,
    ];
    const note = rw.matched_note_id ? this.noteById(rw.matched_note_id) : null;
    if (note) lines.push("", `> **${note.note_id}** (${note.date}): ${note.note}`);
    else lines.push("", rw.reason);
    return lines.join("\n");
  }

  anomalyList(rows, heading) {
    const lines = [heading, "", ...rows.map((rw) => this.bullet(rw))];
    const cited = [];
    for (const rw of rows) {
      if (rw.matched_note_id && !cited.includes(rw.matched_note_id)) cited.push(rw.matched_note_id);
    }
    if (cited.length) {
      lines.push("", "**Evidence**");
      for (const nid of cited) {
        const note = this.noteById(nid);
        if (note) lines.push(`> **${nid}** (${note.date}): ${note.note}`);
      }
    }
    return lines.join("\n");
  }

  // ── intents ────────────────────────────────────────────────────────────────
  summary() {
    const m = this.meta;
    const justified = this.anomalies.filter((a) => a.matched_note_id);
    const routes = [...new Set(this.anomalies.map((a) => a.route))].sort();
    return [
      `**Anomaly summary** (${monthLabel(m.date_from)} to ${monthLabel(m.date_to)}, ` +
        `${groupDigits(m.n_shipments)} shipments across ${m.n_routes} routes)`,
      `- Anomalous route-weeks: **${this.anomalies.length}**`,
      `- Justified by a context note: **${justified.length}**`,
      `- Needs human review: **${this.anomalies.length - justified.length}**`,
      `- Routes affected: ${routes.length ? routes.join(", ") : "none"}`,
      "",
      `A week is flagged when its cost per tonne-km is at least ` +
        `${m.own_history_threshold_pct}% above the route's trailing ` +
        `${m.rolling_window_weeks}-week average **and** at least ` +
        `${m.peer_threshold_pct}% above similar routes that same week.`,
    ].join("\n");
  }

  ranking(routes, cheapest) {
    const pool = this.routes
      .filter((r) => !routes.length || routes.includes(r.route))
      .sort((a, b) => (cheapest ? a.avg_cptk - b.avg_cptk : b.avg_cptk - a.avg_cptk));
    const title = cheapest ? "cheapest" : "most expensive";
    const lines = [`**Routes ranked ${title} first** (average cost per tonne-km, all weeks)`, ""];
    pool.forEach((r, i) => {
      const n = r.n_anomalies;
      lines.push(
        `${i + 1}. **${r.route}** (${r.route_type}): ₹${r.avg_cptk_label}/t-km, ` +
          `${n} anomalous week${n === 1 ? "" : "s"}`,
      );
    });
    lines.push("", "Distance bands differ, so compare routes within the same type.");
    return lines.join("\n");
  }

  noteAnswer(ids) {
    const blocks = [];
    for (const nid of ids) {
      const note = this.noteById(nid);
      if (!note) {
        blocks.push(`There is no note ${nid} in the dataset.`);
        continue;
      }
      const sig = note.signals;
      const pos = sig.positive.map((p) => `+${p}`).join(", ") || "none";
      const neg = sig.negative.map((p) => `-${p}`).join(", ") || "none";
      const stance = sig.net > 0 ? "a cost driver" : "not a cost driver";
      const net = sig.net >= 0 ? `+${sig.net}` : String(sig.net);
      const weeks = this.anomalies.filter((a) => a.matched_note_id === nid).map((a) => a.week_of);
      const lines = [
        `**${nid}** (${note.date}, applies to ${note.applies_to})`,
        `> ${note.note}`,
        "",
        `Signals: ${pos}; ${neg} (net ${net}), so it reads as ${stance}.`,
      ];
      lines.push(
        weeks.length
          ? `It explains the flagged weeks of ${weeks.join(", ")}.`
          : "It does not explain any flagged week.",
      );
      blocks.push(lines.join("\n"));
    }
    return blocks.join("\n\n");
  }

  routeAnswer(routes, month, year) {
    const period = Assistant.periodLabel(month, year);
    const hits = this.anomalies.filter(
      (a) => routes.includes(a.route) && Assistant.inPeriod(a.week_of, month, year),
    );
    if (hits.length === 1) return this.detail(hits[0]);
    if (hits.length) {
      const label = routes.length === 1 ? routes[0] : routes.join(", ");
      return this.anomalyList(hits, `Found **${hits.length}** anomalous weeks on ${label}${period}:`);
    }

    const blocks = [];
    for (const name of routes) {
      const r = this.routes.find((x) => x.route === name);
      if (!(month || year)) {
        blocks.push(
          `**${name}** had no anomalous weeks. Across ${r.n_weeks} weeks it averaged ` +
            `₹${r.avg_cptk_label}/t-km, peaking at ₹${r.peak_cptk_label} in the week of ` +
            `${r.peak_week}.`,
        );
        continue;
      }
      const weeks = this.routeWeeks.filter(
        (w) => w.route === name && Assistant.inPeriod(w.week_of, month, year),
      );
      if (!weeks.length) {
        blocks.push(`**${name}** has no shipments${period}.`);
        continue;
      }
      const lines = [`**${name}** had no anomalous weeks${period}. Weekly cost per tonne-km:`];
      for (const w of weeks.slice(0, 8)) {
        const extra = w.own_label ? ` (${w.own_label} vs own history)` : "";
        lines.push(`- ${w.week_of}: ₹${w.cptk_label}${extra}`);
      }
      if (weeks.length > 8) lines.push(`- ...and ${weeks.length - 8} more weeks`);
      blocks.push(lines.join("\n"));
    }
    return blocks.join("\n\n");
  }

  answer(query) {
    const q = query.toLowerCase().trim();
    const tokens = q.match(/[a-z]+|\d+/g) || [];
    const words = new Set(tokens);
    if (!q) return HELP;

    const noteIds = [...q.matchAll(/\bn0*(\d{1,3})\b/g)].map(
      (m) => `N${String(Number(m[1])).padStart(3, "0")}`,
    );
    if (noteIds.length) return this.noteAnswer([...new Set(noteIds)]);

    if (intersects(words, ["help", "hello", "hi", "hey"]) || q.includes("what can you")) return HELP;

    const [month, year] = Assistant.parsePeriod(tokens);
    const routes = this.parseRoutes(q);

    if ((intersects(words, ["summary", "overview", "total"]) || q.includes("how many")) && !routes.length) {
      return this.summary();
    }

    if (intersects(words, ["cheapest", "lowest", "cheaper"])) return this.ranking(routes, true);
    if (intersects(words, ["expensive", "highest", "costliest", "rank", "ranking", "compare"])) {
      return this.ranking(routes, false);
    }

    const pool = this.anomalies.filter(
      (a) => (!routes.length || routes.includes(a.route)) && Assistant.inPeriod(a.week_of, month, year),
    );
    const period = Assistant.periodLabel(month, year);
    if (intersects(words, ["unexplained", "review", "suspicious", "investigate", "unjustified", "flagged"])) {
      const rows = pool.filter((a) => !a.matched_note_id);
      if (!rows.length) return `No unexplained anomalies${period}. Every flagged week has a supporting note.`;
      if (rows.length === 1 && routes.length) return this.detail(rows[0]);
      return this.anomalyList(rows, `**${rows.length}** week(s) need human review${period}:`);
    }
    if (intersects(words, ["justified", "explained", "explains", "legitimate"])) {
      const rows = pool.filter((a) => a.matched_note_id);
      if (!rows.length) return `No justified anomalies${period}.`;
      if (rows.length === 1 && routes.length) return this.detail(rows[0]);
      return this.anomalyList(rows, `**${rows.length}** anomalous week(s) are explained by a note${period}:`);
    }

    if (routes.length) return this.routeAnswer(routes, month, year);
    if (month || year) {
      if (!pool.length) return `No anomalous weeks${period}.`;
      return this.anomalyList(pool, `Found **${pool.length}** anomalous week(s)${period}:`);
    }

    const hits = this.retrieve(query);
    if (hits.length) {
      const lines = ["I couldn't tie that to a route or period, but these notes look relevant:", ""];
      for (const n of hits) lines.push(`> **${n.note_id}** (${n.date}, ${n.applies_to}): ${n.note}`);
      return lines.join("\n");
    }
    return HELP;
  }
}
