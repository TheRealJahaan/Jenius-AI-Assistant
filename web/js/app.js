import { Assistant } from "./assistant.js";
import { renderChart } from "./chart.js";
import { escapeHtml, renderMarkdown } from "./markdown.js";

const $ = (sel) => document.querySelector(sel);
const fmtInt = (n) => n.toLocaleString("en-IN");

const QUICK_PROMPTS = [
  "Give me a summary of all anomalies",
  "Which weeks need human review?",
  "Why did Chennai-Bangalore get pricier in February 2025?",
  "Which route is the most expensive?",
  "What does note N009 say?",
];

const state = {
  kb: null,
  bot: null,
  own: 15,
  peer: 15,
  route: null,
  focusWeek: null,
  selectedKey: null,
  api: false,
};

const isFlagged = (r) =>
  r.pct_vs_own != null && r.pct_vs_peers != null && r.pct_vs_own >= state.own && r.pct_vs_peers >= state.peer;

const flaggedRows = () => state.kb.route_weeks.filter(isFlagged);

// ── KPIs ───────────────────────────────────────────────────────────────────
function renderKpis() {
  const { meta } = state.kb;
  const rows = flaggedRows();
  const review = rows.filter((r) => !r.matched_note_id).length;
  const tiles = [
    { label: "Shipments analysed", value: fmtInt(meta.n_shipments), note: `${meta.date_from} → ${meta.date_to}` },
    { label: "Route-weeks scored", value: fmtInt(meta.n_route_weeks), note: `${meta.n_routes} routes · ${meta.n_transporters} transporters` },
    { label: "Flagged weeks", value: rows.length, note: `${rows.length - review} justified by a note` },
    { label: "Need human review", value: review, note: review ? "No supporting evidence" : "Every flag is explained", tone: review ? "review" : "ok" },
  ];
  $("#kpis").innerHTML = tiles
    .map(
      (t) => `<div class="kpi ${t.tone ? `kpi-${t.tone}` : ""}">
        <div class="kpi-label">${t.label}</div>
        <div class="kpi-value">${t.value}</div>
        <div class="kpi-note">${escapeHtml(t.note)}</div>
      </div>`,
    )
    .join("");
}

// ── Table ──────────────────────────────────────────────────────────────────
function verdictBadge(r) {
  return r.matched_note_id
    ? `<span class="badge badge-justified">✅ Justified · ${escapeHtml(r.matched_note_id)}</span>`
    : `<span class="badge badge-review">⚠️ Needs review</span>`;
}

function renderTable() {
  const rows = flaggedRows();
  const body = $("#anomalyRows");
  $("#ruleText").textContent =
    `Weeks at least ${state.own}% above the route's trailing ${state.kb.meta.rolling_window_weeks}-week average ` +
    `and at least ${state.peer}% above similar routes. ${rows.length} match.`;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">No week clears both thresholds. Try lowering them.</td></tr>`;
    $("#detail").hidden = true;
    return;
  }
  body.innerHTML = rows
    .map((r) => {
      const key = `${r.route}|${r.week_of}`;
      return `<tr tabindex="0" data-key="${escapeHtml(key)}" class="${key === state.selectedKey ? "selected" : ""}">
        <th scope="row">${escapeHtml(r.route)}</th>
        <td>${r.week_of}</td>
        <td class="num">₹${r.cptk_label}</td>
        <td class="num">${r.own_label}</td>
        <td class="num">${r.peer_label}</td>
        <td>${verdictBadge(r)}</td>
      </tr>`;
    })
    .join("");
  if (state.selectedKey && !rows.some((r) => `${r.route}|${r.week_of}` === state.selectedKey)) {
    state.selectedKey = null;
    $("#detail").hidden = true;
  }
}

function showDetail(key) {
  const r = state.kb.route_weeks.find((x) => `${x.route}|${x.week_of}` === key);
  if (!r) return;
  state.selectedKey = key;
  const note = r.matched_note_id ? state.kb.notes.find((n) => n.note_id === r.matched_note_id) : null;
  const detail = $("#detail");
  detail.hidden = false;
  detail.innerHTML = `
    <div class="detail-head">
      <h3>${escapeHtml(r.route)} · week of ${r.week_of}</h3>
      ${verdictBadge(r)}
    </div>
    <dl class="detail-grid">
      <div><dt>Cost</dt><dd>₹${r.cptk_label} per tonne-km across ${r.n_shipments} loads</dd></div>
      <div><dt>vs own history</dt><dd>${escapeHtml(r.vs_own_history)}</dd></div>
      <div><dt>vs similar routes</dt><dd>${escapeHtml(r.vs_similar_routes)}</dd></div>
      <div><dt>What drove it</dt><dd>${escapeHtml(r.drivers)}</dd></div>
    </dl>
    ${note ? `<blockquote><b>${note.note_id}</b> (${note.date}): ${escapeHtml(note.note)}</blockquote>` : `<p class="reason">${escapeHtml(r.reason)}</p>`}`;
  document.querySelectorAll("#anomalyRows tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.key === key));
  selectRoute(r.route, r.week_of);
}

function downloadCsv() {
  const cols = ["route", "week_of", "cost_per_tonne_km", "vs_own_history", "vs_similar_routes", "flagged", "matched_note_id", "reason"];
  const quote = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const lines = [cols.join(",")];
  for (const r of flaggedRows()) {
    const vals = [r.route, r.week_of, r.cptk_label, r.vs_own_history, r.vs_similar_routes, r.flagged, r.matched_note_id, r.reason];
    lines.push(vals.map((v) => quote(String(v ?? ""))).join(","));
  }
  const blob = new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `jenius_anomalies_own${state.own}_peer${state.peer}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Explorer ───────────────────────────────────────────────────────────────
function renderChips() {
  $("#routeChips").innerHTML = state.kb.routes
    .map((r) => {
      const n = state.kb.route_weeks.filter((w) => w.route === r.route && isFlagged(w)).length;
      const active = r.route === state.route;
      return `<button type="button" role="tab" class="chip ${active ? "active" : ""}" aria-selected="${active}" data-route="${escapeHtml(r.route)}">
        ${escapeHtml(r.route)}${n ? `<span class="chip-count">${n}</span>` : ""}</button>`;
    })
    .join("");
}

function renderExplorer() {
  const weeks = state.kb.route_weeks.filter((w) => w.route === state.route);
  renderChart($("#chart"), weeks, isFlagged, state.focusWeek);
  const r = state.kb.routes.find((x) => x.route === state.route);
  const n = weeks.filter(isFlagged).length;
  $("#routeStats").innerHTML = `
    <div><span>Length band</span><b>${escapeHtml(r.route_type)}</b></div>
    <div><span>Average</span><b>₹${r.avg_cptk_label}/t-km</b></div>
    <div><span>Peak week</span><b>₹${r.peak_cptk_label} · ${r.peak_week}</b></div>
    <div><span>Shipments</span><b>${fmtInt(r.n_shipments)}</b></div>
    <div><span>Flagged weeks</span><b>${n}</b></div>`;
}

function selectRoute(route, focusWeek = null) {
  state.route = route;
  state.focusWeek = focusWeek;
  renderChips();
  renderExplorer();
}

// ── Assistant ──────────────────────────────────────────────────────────────
function addBubble(role, html) {
  const div = document.createElement("div");
  div.className = `bubble bubble-${role}`;
  div.innerHTML = html;
  $("#chatLog").appendChild(div);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  return div;
}

async function ask(question) {
  question = question.trim().slice(0, 500);
  if (!question) return;
  addBubble("user", escapeHtml(question));
  let answer = null;
  if (state.api) {
    try {
      const res = await fetch("api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (res.ok) answer = (await res.json()).answer;
    } catch {
      /* fall through to the in-browser engine */
    }
  }
  addBubble("bot", renderMarkdown(answer ?? state.bot.answer(question)));
}

async function detectApi() {
  try {
    const res = await fetch("api/health", { cache: "no-store" });
    state.api = res.ok && (res.headers.get("content-type") || "").includes("json");
  } catch {
    state.api = false;
  }
  $("#engineStatus").textContent = state.api ? "Engine: Python API" : "Engine: in-browser";
}

// ── Theme ──────────────────────────────────────────────────────────────────
function toggleTheme() {
  const root = document.documentElement;
  const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  root.dataset.theme = current === "dark" ? "light" : "dark";
  try {
    localStorage.setItem("jenius-theme", root.dataset.theme);
  } catch {
    /* storage unavailable: theme still applies for this visit */
  }
  renderExplorer();
}

// ── Wiring ─────────────────────────────────────────────────────────────────
function refresh() {
  $("#ownOut").textContent = `${state.own}%`;
  $("#peerOut").textContent = `${state.peer}%`;
  renderKpis();
  renderTable();
  renderChips();
  renderExplorer();
}

async function main() {
  const res = await fetch("data/kb.json");
  if (!res.ok) throw new Error(`Could not load data/kb.json (${res.status})`);
  state.kb = await res.json();
  state.bot = new Assistant(state.kb);
  state.own = state.kb.meta.own_history_threshold_pct;
  state.peer = state.kb.meta.peer_threshold_pct;
  $("#ownRange").value = state.own;
  $("#peerRange").value = state.peer;
  state.route = (state.kb.routes.find((r) => r.n_anomalies) || state.kb.routes[0]).route;

  const m = state.kb.meta;
  $("#footnote").textContent =
    `Defaults: ${m.own_history_threshold_pct}% / ${m.peer_threshold_pct}% thresholds, ${m.rolling_window_weeks}-week window, ` +
    `±${m.note_window_days}-day note window. The sliders re-filter precomputed scores; the CLI re-runs the full pipeline.`;
  $("#buildInfo").textContent = `v${m.version} · data ${m.date_from} to ${m.date_to}`;

  $("#ownRange").addEventListener("input", (e) => {
    state.own = Number(e.target.value);
    refresh();
  });
  $("#peerRange").addEventListener("input", (e) => {
    state.peer = Number(e.target.value);
    refresh();
  });
  $("#resetThresholds").addEventListener("click", () => {
    state.own = m.own_history_threshold_pct;
    state.peer = m.peer_threshold_pct;
    $("#ownRange").value = state.own;
    $("#peerRange").value = state.peer;
    refresh();
  });
  $("#downloadCsv").addEventListener("click", downloadCsv);
  $("#anomalyRows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-key]");
    if (tr) showDetail(tr.dataset.key);
  });
  $("#anomalyRows").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-key]");
    if (tr && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      showDetail(tr.dataset.key);
    }
  });
  $("#routeChips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-route]");
    if (chip) selectRoute(chip.dataset.route);
  });
  $("#themeToggle").addEventListener("click", toggleTheme);

  $("#quickPrompts").innerHTML = QUICK_PROMPTS.map(
    (q) => `<button type="button" class="chip" data-q="${escapeHtml(q)}">${escapeHtml(q)}</button>`,
  ).join("");
  $("#quickPrompts").addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]");
    if (b) ask(b.dataset.q);
  });
  $("#chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#chatInput");
    ask(input.value);
    input.value = "";
  });
  addBubble(
    "bot",
    renderMarkdown(
      `Hi! I've indexed **${fmtInt(m.n_shipments)} shipments** and **${state.kb.notes.length} field notes**. ` +
        "Ask about a route, a month, a note, or which spikes need review.",
    ),
  );

  let resizeTimer;
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderExplorer, 120);
  }).observe($("#chart"));

  refresh();
  detectApi();
}

main().catch((err) => {
  console.error(err);
  $("#kpis").innerHTML = `<div class="error">Failed to load data: ${escapeHtml(err.message)}. Serve the site over HTTP (e.g. <code>python -m jenius serve</code>).</div>`;
});
