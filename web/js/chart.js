// Dependency-free SVG line chart for one route's weekly cost per tonne-km,
// with the two comparison baselines and flagged weeks marked. Includes a
// crosshair + tooltip hover layer and keyboard navigation.

import { escapeHtml } from "./markdown.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const BASE_MARGIN = { top: 16, right: 92, bottom: 30, left: 48 };

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(min, max, count = 5) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const ticks = [];
  for (let v = Math.floor(min / step) * step; v <= max + step / 2; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtMonth = (iso) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(2, 4)}`;
const fmtPct = (p) => (p == null ? "n/a" : `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`);
const ownPct = (r) => r.own_label ?? fmtPct(r.pct_vs_own);
const peerPct = (r) => r.peer_label ?? fmtPct(r.pct_vs_peers);

/**
 * @param {HTMLElement} root  container; the chart fills its width
 * @param {object[]} weeks    route_weeks rows for one route, sorted by week
 * @param {(row: object) => boolean} isFlagged
 * @param {string|null} focusWeek  week_of to highlight (e.g. from the table)
 */
export function renderChart(root, weeks, isFlagged, focusWeek = null) {
  root.innerHTML = "";
  const width = Math.max(root.clientWidth, 300);
  // Narrow screens drop the direct labels (the legend above still names every series).
  const compact = width < 560;
  const MARGIN = { ...BASE_MARGIN, right: compact ? 14 : BASE_MARGIN.right };
  const height = Math.round(Math.min(380, Math.max(240, width * 0.42)));
  const w = width - MARGIN.left - MARGIN.right;
  const h = height - MARGIN.top - MARGIN.bottom;

  const values = weeks.flatMap((r) => [r.cptk, r.baseline, r.peer_avg]).filter((v) => v != null);
  const yTicks = niceTicks(Math.min(...values) * 0.97, Math.max(...values) * 1.02);
  const y0 = yTicks[0];
  const y1 = yTicks[yTicks.length - 1];
  const x = (i) => MARGIN.left + (weeks.length === 1 ? w / 2 : (i / (weeks.length - 1)) * w);
  const y = (v) => MARGIN.top + h - ((v - y0) / (y1 - y0)) * h;

  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    role: "img",
    "aria-label": `Weekly cost per tonne-km for ${weeks[0]?.route ?? "route"}`,
    class: "chart-svg",
  });
  root.appendChild(svg);

  // Grid + y axis
  const grid = el("g", { class: "chart-grid" }, svg);
  for (const t of yTicks) {
    el("line", { x1: MARGIN.left, x2: MARGIN.left + w, y1: y(t), y2: y(t) }, grid);
    const label = el("text", { x: MARGIN.left - 8, y: y(t), dy: "0.32em", "text-anchor": "end" }, grid);
    label.textContent = `₹${t.toFixed(2)}`;
  }
  // x axis: one label per quarter (half-year on narrow screens)
  const xAxis = el("g", { class: "chart-axis" }, svg);
  weeks.forEach((r, i) => {
    const m = Number(r.week_of.slice(5, 7));
    const prev = i > 0 ? Number(weeks[i - 1].week_of.slice(5, 7)) : null;
    if (m !== prev && (m - 1) % (compact ? 6 : 3) === 0) {
      const t = el("text", { x: x(i), y: MARGIN.top + h + 20, "text-anchor": "middle" }, xAxis);
      t.textContent = fmtMonth(r.week_of);
      el("line", { x1: x(i), x2: x(i), y1: MARGIN.top + h, y2: MARGIN.top + h + 5 }, xAxis);
    }
  });

  // Series: peers, baseline (reference), actual
  const path = (key) => {
    let d = "";
    let pen = false;
    weeks.forEach((r, i) => {
      const v = r[key];
      if (v == null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const series = [
    { key: "peer_avg", cls: "line-peers", label: "Similar routes" },
    { key: "baseline", cls: "line-baseline", label: "8-wk baseline" },
    { key: "cptk", cls: "line-actual", label: "This route" },
  ];
  for (const s of series) el("path", { d: path(s.key), class: `chart-line ${s.cls}` }, svg);

  // Direct labels at the right edge, nudged apart to avoid collisions
  const last = weeks.length - 1;
  const placed = series
    .map((s) => {
      let i = last;
      while (i > 0 && weeks[i][s.key] == null) i--;
      return { ...s, y: y(weeks[i][s.key]) };
    })
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < placed.length; i++) placed[i].y = Math.max(placed[i].y, placed[i - 1].y + 14);
  for (const p of compact ? [] : placed) {
    const t = el("text", { x: MARGIN.left + w + 8, y: p.y, dy: "0.32em", class: `direct-label ${p.cls}` }, svg);
    t.textContent = p.label;
  }

  // Flagged weeks
  const marks = el("g", {}, svg);
  weeks.forEach((r, i) => {
    if (!isFlagged(r)) return;
    const cls = r.matched_note_id ? "mark-justified" : "mark-review";
    el("circle", { cx: x(i), cy: y(r.cptk), r: 5.5, class: `chart-mark ${cls}` }, marks);
  });

  // Focus ring for a week selected elsewhere
  const focusIdx = focusWeek ? weeks.findIndex((r) => r.week_of === focusWeek) : -1;
  if (focusIdx >= 0) {
    el("circle", { cx: x(focusIdx), cy: y(weeks[focusIdx].cptk), r: 11, class: "chart-focus" }, svg);
  }

  // Hover layer
  const cross = el("line", { y1: MARGIN.top, y2: MARGIN.top + h, class: "chart-crosshair" }, svg);
  const dot = el("circle", { r: 4.5, class: "chart-hover-dot" }, svg);
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.setAttribute("role", "status");
  root.appendChild(tip);
  const overlay = el("rect", {
    x: MARGIN.left - 6,
    y: MARGIN.top,
    width: w + 12,
    height: h,
    fill: "transparent",
    tabindex: "0",
    "aria-label": "Chart: use left and right arrow keys to move between weeks",
  }, svg); // prettier-ignore

  let current = focusIdx >= 0 ? focusIdx : last;
  const show = (i) => {
    current = Math.max(0, Math.min(last, i));
    const r = weeks[current];
    cross.setAttribute("x1", x(current));
    cross.setAttribute("x2", x(current));
    dot.setAttribute("cx", x(current));
    dot.setAttribute("cy", y(r.cptk));
    svg.classList.add("hovering");
    const flagged = isFlagged(r);
    const status = !flagged
      ? ""
      : r.matched_note_id
        ? `<div class="tip-status justified">✅ Flagged, justified by ${escapeHtml(r.matched_note_id)}</div>`
        : `<div class="tip-status review">⚠️ Flagged, needs review</div>`;
    tip.innerHTML = `
      <div class="tip-title">Week of ${escapeHtml(r.week_of)}</div>
      <div class="tip-row"><span class="sw line-actual"></span>This route<b>₹${r.cptk.toFixed(2)}</b></div>
      <div class="tip-row"><span class="sw line-baseline"></span>8-wk baseline<b>${r.baseline == null ? "n/a" : `₹${r.baseline.toFixed(2)}`}</b></div>
      <div class="tip-row"><span class="sw line-peers"></span>Similar routes<b>${r.peer_avg == null ? "n/a" : `₹${r.peer_avg.toFixed(2)}`}</b></div>
      <div class="tip-delta">${ownPct(r)} vs own · ${peerPct(r)} vs peers · ${r.n_shipments} loads</div>
      ${status}`;
    const px = (x(current) / width) * root.clientWidth;
    const tipW = tip.offsetWidth || 220;
    tip.style.left = `${Math.min(Math.max(px - tipW / 2, 0), root.clientWidth - tipW)}px`;
    tip.style.top = `${(MARGIN.top / height) * root.clientHeight}px`;
    tip.classList.add("visible");
  };
  const hide = () => {
    svg.classList.remove("hovering");
    tip.classList.remove("visible");
  };
  const indexAt = (evt) => {
    const rect = svg.getBoundingClientRect();
    const px = ((evt.clientX - rect.left) / rect.width) * width;
    return Math.round(((px - MARGIN.left) / w) * last);
  };
  overlay.addEventListener("pointermove", (e) => show(indexAt(e)));
  overlay.addEventListener("pointerleave", hide);
  overlay.addEventListener("focus", () => show(current));
  overlay.addEventListener("blur", hide);
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      show(current + (e.key === "ArrowRight" ? 1 : -1));
    }
  });
}
