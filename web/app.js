/* Jenius web app. Talks only to its own API (/api/*). No inline handlers: the page runs under a strict
   Content-Security-Policy, so every listener is attached here. Anything that came from a user or the AI
   is escaped before it touches the page. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    config: null,
    user: null,
    datasets: [],
    dataset: null, // the selected dataset's detail
    conversationId: null,
    cycle: "monthly",
    sending: false,
  };

  // ── API ──────────────────────────────────────────────────────────────────

  class ApiError extends Error {
    constructor(message, status, problems) {
      super(message);
      this.status = status;
      this.problems = problems || [];
    }
  }

  async function api(path, { method = "GET", json, form } = {}) {
    const opts = { method, credentials: "same-origin", headers: {} };
    if (json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(json);
    } else if (form) {
      opts.body = form;
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch {
      throw new ApiError("Can't reach Jenius. Check your connection and try again.", 0);
    }
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const body = isJson ? await res.json() : null;
    if (!res.ok) {
      throw new ApiError((body && body.error) || `Request failed (${res.status}).`, res.status, body && body.problems);
    }
    return body;
  }

  // ── safe rendering ───────────────────────────────────────────────────────

  function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function inline(text) {
    // `text` is already escaped. Only a few markdown marks become tags; nothing can create links or attributes.
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[(N?[A-Z]*\d{1,5})\]/g, '<span class="cite">[$1]</span>');
  }

  function markdown(source) {
    const lines = esc(source).replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0;
    const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (isRow(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        const head = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && isRow(lines[i])) rows.push(cells(lines[i++]));
        out.push('<div class="md-table-wrap"><table><thead><tr>' + head.map((h) => `<th>${h}</th>`).join("") +
          "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") +
          "</tbody></table></div>");
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) { out.push(`<h4>${inline(heading[2])}</h4>`); i++; continue; }
      if (/^&gt;\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^&gt;\s?/.test(lines[i])) quote.push(inline(lines[i++].replace(/^&gt;\s?/, "")));
        out.push(`<blockquote>${quote.join("<br>")}</blockquote>`);
        continue;
      }
      if (/^\s*([-*•]|\d+\.)\s+/.test(line)) {
        const ordered = /^\s*\d+\./.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*•]|\d+\.)\s+/.test(lines[i])) {
          items.push(`<li>${inline(lines[i++].replace(/^\s*([-*•]|\d+\.)\s+/, ""))}</li>`);
        }
        out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !isRow(lines[i]) && !/^(#{1,4}\s|&gt;|\s*([-*•]|\d+\.)\s)/.test(lines[i])) {
        para.push(inline(lines[i++]));
      }
      if (para.length) out.push(`<p>${para.join("<br>")}</p>`);
      else i++;
    }
    return out.join("");
  }

  const inr = (n) => "₹" + Math.round(Number(n) || 0).toLocaleString("en-IN");
  const num = (n) => (Number(n) || 0).toLocaleString("en-IN");

  function showError(container, err) {
    const box = $("[data-error]", container);
    if (!box) return;
    if (!err) { box.classList.add("hidden"); box.textContent = ""; return; }
    box.innerHTML = esc(err.message) + (err.problems && err.problems.length > 1
      ? "<ul>" + err.problems.map((p) => `<li>${esc(p)}</li>`).join("") + "</ul>" : "");
    box.classList.remove("hidden");
  }

  function showOk(container, message) {
    const box = $("[data-ok]", container);
    if (!box) return;
    box.textContent = message || "";
    box.classList.toggle("hidden", !message);
  }

  async function withBusy(button, fn) {
    if (button) button.disabled = true;
    try { return await fn(); } finally { if (button) button.disabled = false; }
  }

  // ── navigation ───────────────────────────────────────────────────────────

  const PAGES = ["landing", "login", "signup", "reset", "assistant", "pricing", "account", "admin", "privacy"];
  const NEEDS_LOGIN = new Set(["account", "admin"]);

  function showPage(page, { push = true } = {}) {
    if (!PAGES.includes(page)) page = "landing";
    if (NEEDS_LOGIN.has(page) && !state.user) page = "login";
    if (page === "admin" && state.user && !state.user.is_admin && !showPage.rechecked) {
      showPage.rechecked = true; // admin rights may have been granted since this page loaded
      return refreshUser().then(() => showPage("admin", { push }));
    }
    showPage.rechecked = false;
    if (page === "admin" && !(state.user && state.user.is_admin)) page = "assistant";
    $$(".page-view").forEach((v) => v.classList.toggle("active", v.id === "view-" + page));
    $$(".nav-link").forEach((l) => l.classList.toggle("active", l.dataset.page === page));
    if (push && location.hash !== "#/" + page) history.pushState(null, "", "#/" + page);
    window.scrollTo({ top: 0, behavior: "instant" });
    if (page === "assistant") loadDashboard();
    if (page === "account") loadAccount();
    if (page === "admin") loadAdmin();
    if (page === "login") setTimeout(() => $("#loginEmail").focus(), 50);
  }

  function pageFromHash() {
    const resetMatch = location.hash.match(/^#reset=([A-Za-z0-9_-]{20,100})$/);
    if (resetMatch) {
      state.resetToken = resetMatch[1];
      history.replaceState(null, "", "#/reset"); // the token leaves the address bar right away
      $("#resetRequestCard").classList.add("hidden");
      $("#resetConfirmCard").classList.remove("hidden");
      return "reset";
    }
    return (location.hash.match(/^#\/(\w+)/) || [])[1] || "landing";
  }

  // ── session ──────────────────────────────────────────────────────────────

  function setUser(user) {
    state.user = user;
    $("#navGuest").classList.toggle("hidden", !!user);
    $("#navUser").classList.toggle("hidden", !user);
    $("#navAdmin").classList.toggle("hidden", !(user && user.is_admin));
    $("#guestSignupBtn").classList.toggle("hidden", !!user);
    $("#upgradeBtn").classList.toggle("hidden", !user || user.plan === "pro");
    $("#newChatBtn").classList.toggle("hidden", !user);
    $("#dashUserName").textContent = user ? user.name : "Guest";
    $("#dashUserEmail").textContent = user ? user.email : "Viewing the public demo";
    $("#dashUserAvatar").textContent = (user ? user.name : "J").charAt(0).toUpperCase();
    const badge = $("#currentPlanBadge");
    badge.textContent = user ? (user.plan === "pro" ? "PRO" : "FREE PLAN") : "DEMO";
    badge.className = "plan-tag" + (user && user.plan === "pro" ? " pro" : "");
    updateQuota();
  }

  function updateQuota(remaining) {
    const label = $("#quotaLabel");
    const status = $("#chatStatus");
    if (!state.config) return;
    if (!state.config.llm_enabled) {
      label.textContent = "";
      status.innerHTML = '<span style="color:#10B981">● Quick answers</span>';
      return;
    }
    if (!state.user) {
      label.textContent = "";
      status.innerHTML = '<span class="muted">● Quick answers · log in for Claude</span>';
      return;
    }
    const limits = state.user.limits;
    if (remaining !== undefined && remaining !== null) limits.used_today = limits.daily_messages - remaining;
    const left = Math.max(0, limits.daily_messages - limits.used_today);
    label.textContent = `${left} of ${limits.daily_messages} Claude questions left today`;
    status.innerHTML = left > 0 ? '<span style="color:#10B981">● Claude</span>' : '<span class="muted">● Quick answers (limit reached)</span>';
  }

  async function refreshUser() {
    try { setUser(await api("/api/auth/me")); } catch { setUser(null); }
  }

  async function afterAuth(user) {
    setUser(user);
    state.conversationId = null;
    state.datasets = [];
    state.dataset = null;
    showPage("assistant");
  }

  // ── dashboard ────────────────────────────────────────────────────────────

  async function loadDashboard() {
    try {
      state.datasets = await api("/api/datasets");
    } catch (err) {
      renderChatIntro(err.message);
      return;
    }
    const select = $("#datasetSelect");
    const keep = state.dataset && state.datasets.find((d) => d.id === state.dataset.id);
    select.innerHTML = state.datasets.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
    if (!state.datasets.length) {
      renderChatIntro("No datasets yet. Upload one to get started.");
      return;
    }
    const chosen = keep ? keep.id : (state.datasets.find((d) => d.is_mine) || state.datasets[0]).id;
    select.value = chosen;
    await selectDataset(chosen, !keep);
  }

  async function selectDataset(id, resetChat = true) {
    try {
      state.dataset = await api(`/api/datasets/${encodeURIComponent(id)}`);
    } catch (err) {
      renderChatIntro(err.message);
      return;
    }
    const d = state.dataset;
    $("#datasetMeta").textContent = d.first_date ? `${d.first_date} → ${d.last_date}` : "";
    $("#deleteDatasetBtn").classList.toggle("hidden", !d.is_mine);
    $("#mShipments").textContent = num(d.shipment_count);
    $("#mShipmentsNote").textContent = `${d.routes.length} routes · ${d.note_count} context notes`;
    $("#mSpend").textContent = inr(d.total_freight_cost_inr);
    $("#mSpendNote").textContent = d.shipment_count ? `avg ${inr(d.total_freight_cost_inr / d.shipment_count)} per shipment` : "";
    $("#mAnomalies").textContent = num(d.anomaly_count);
    $("#mAnomaliesNote").textContent = `${d.justified} explained by a note`;
    $("#mReview").textContent = num(d.needs_review);
    if (resetChat) {
      state.conversationId = null;
      renderChatIntro();
    }
    loadAnomalies();
  }

  async function loadAnomalies() {
    if (!state.dataset) return;
    const list = $("#anomalyList");
    const filter = $("#anomalyFilter").value;
    let rows;
    try {
      rows = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/anomalies?status=${encodeURIComponent(filter)}`);
    } catch (err) {
      list.innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      return;
    }
    if (!rows.length) {
      list.innerHTML = '<p class="small muted">No anomalies here. Costs look in line with history and peers.</p>';
      return;
    }
    const mine = state.dataset.is_mine;
    list.innerHTML = rows.map((a) => {
      const verdict = a.needs_review ? '<span class="pill review">Needs review</span>' : '<span class="pill ok">Justified</span>';
      const status = a.review_status !== "open" ? ` <span class="pill neutral">${esc(a.review_status)}</span>` : "";
      const review = mine ? `<div class="review">
          <button class="btn btn-ghost btn-sm" data-review="confirmed" data-id="${a.id}">Confirm</button>
          <button class="btn btn-ghost btn-sm" data-review="dismissed" data-id="${a.id}">Dismiss</button>
          ${a.review_status !== "open" ? `<button class="btn btn-ghost btn-sm" data-review="open" data-id="${a.id}">Reopen</button>` : ""}
        </div>` : "";
      return `<div class="anomaly-item${a.needs_review ? "" : " justified"}" data-anomaly="${a.id}"
                   data-route="${esc(a.route)}" data-week="${esc(a.week_of)}">
          <div class="head"><span>${esc(a.route)}</span><span>${verdict}${status}</span></div>
          <div>Week of ${esc(a.week_of)} · ₹${Number(a.cost_per_tonne_km).toFixed(2)}/t-km ·
            ${a.pct_vs_own >= 0 ? "+" : ""}${a.pct_vs_own.toFixed(1)}% vs history</div>
          ${a.matched_note_code ? `<div class="small muted">Note ${esc(a.matched_note_code)}</div>` : ""}
          ${review}
        </div>`;
    }).join("");
  }

  // ── chat ─────────────────────────────────────────────────────────────────

  function renderChatIntro(extra) {
    const box = $("#chatMessages");
    const who = state.user ? `Hi ${esc(state.user.name.split(" ")[0])}!` : "Hi!";
    const ai = state.config && state.config.llm_enabled && state.user;
    box.innerHTML = "";
    addBubble("assistant", `${who} I'm the Jenius assistant. ${state.dataset
      ? `I'm looking at **${state.dataset.name}**: ${num(state.dataset.shipment_count)} shipments and ${num(state.dataset.anomaly_count)} anomalous route-weeks.`
      : ""}\n\n${ai
      ? "Ask me anything about these shipments. I look up the numbers and notes before I answer."
      : "Ask about a route, a month or the anomalies. " + (state.config && state.config.llm_enabled ? "Log in to ask Claude open-ended follow-up questions." : "")}${extra ? "\n\n" + extra : ""}`);
  }

  function addBubble(role, text, meta) {
    const box = $("#chatMessages");
    const el = document.createElement("div");
    el.className = "msg-bubble " + (role === "user" ? "msg-user" : "msg-assistant");
    if (role === "user") el.textContent = text;
    else el.innerHTML = markdown(text) + (meta ? `<div class="msg-meta">${esc(meta)}</div>` : "");
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    return el;
  }

  async function ask(question) {
    question = (question || "").trim();
    if (!question || state.sending || !state.dataset) return;
    state.sending = true;
    $("#chatSend").disabled = true;
    addBubble("user", question);
    const thinking = addBubble("assistant", "");
    thinking.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
    try {
      const res = await api("/api/chat", { method: "POST", json: {
        dataset_id: state.dataset.id, message: question, conversation_id: state.conversationId,
      } });
      thinking.remove();
      state.conversationId = res.conversation_id;
      const meta = res.mode === "ai"
        ? "Answered by Claude" + (res.tools_used.length ? ` · looked up: ${[...new Set(res.tools_used)].join(", ").replace(/_/g, " ")}` : "")
        : "Quick answer from the built-in engine";
      addBubble("assistant", res.reply, meta);
      if (res.notice) addBubble("assistant", res.notice);
      if (state.user) updateQuota(res.remaining_today);
    } catch (err) {
      thinking.remove();
      addBubble("assistant", "⚠️ " + err.message);
      if (err.status === 401) await refreshUser();
    } finally {
      state.sending = false;
      $("#chatSend").disabled = false;
    }
  }

  // ── account, billing, admin ──────────────────────────────────────────────

  async function loadAccount() {
    await refreshUser();
    const u = state.user;
    if (!u) return showPage("login");
    $("#accountSummary").textContent = `${u.name} · ${u.email} · ${u.plan === "pro" ? "Pro plan" : "Free plan"}` +
      (u.plan_expires_at ? ` until ${new Date(u.plan_expires_at).toLocaleDateString("en-IN")}` : "");
    $("#accountUpgradeBtn").textContent = u.plan === "pro" ? "Extend Pro" : "⚡ Upgrade to Pro";
    try {
      const billing = await api("/api/billing");
      $("#paymentsList").innerHTML = billing.payments.length
        ? '<div class="md-table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Plan</th><th>Amount</th><th>UTR</th><th>Status</th></tr></thead><tbody>' +
          billing.payments.map((p) => `<tr><td>${esc(new Date(p.created_at).toLocaleDateString("en-IN"))}</td><td>Pro (${esc(p.cycle)})</td>
            <td>${inr(p.amount_inr)}</td><td>${esc(p.upi_reference)}</td><td>${esc(p.status)}${p.note ? " · " + esc(p.note) : ""}</td></tr>`).join("") +
          "</tbody></table></div>"
        : "No payments yet.";
    } catch (err) {
      $("#paymentsList").textContent = err.message;
    }
  }

  async function loadAdmin() {
    try {
      const payments = await api("/api/admin/payments?status=pending");
      $("#adminPayments").innerHTML = payments.length
        ? '<div class="md-table-wrap"><table class="data-table"><thead><tr><th>Submitted</th><th>Who</th><th>Amount</th><th>UTR</th><th></th></tr></thead><tbody>' +
          payments.map((p) => `<tr><td>${esc(new Date(p.created_at).toLocaleString("en-IN"))}</td><td>${esc(p.user_name)}<br><span class="muted">${esc(p.user_email)}</span></td>
            <td>${inr(p.amount_inr)} (${esc(p.cycle)})</td><td><code>${esc(p.upi_reference)}</code></td>
            <td><button class="btn btn-primary btn-sm" data-pay="approve" data-id="${esc(p.id)}">Approve</button>
                <button class="btn btn-ghost btn-sm" data-pay="reject" data-id="${esc(p.id)}">Reject</button></td></tr>`).join("") +
          "</tbody></table></div>"
        : "Nothing waiting.";
      const usage = await api("/api/admin/usage");
      $("#adminUsage").innerHTML = `<p>${num(usage.users)} accounts · $${usage.total_cost_usd.toFixed(2)} spent on Claude</p>` +
        (usage.days.length ? '<div class="md-table-wrap"><table class="data-table"><thead><tr><th>Day</th><th>Calls</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead><tbody>' +
          usage.days.map((d) => `<tr><td>${esc(d.date)}</td><td>${num(d.calls)}</td><td>${num(d.input_tokens)}</td><td>${num(d.output_tokens)}</td><td>$${d.cost_usd.toFixed(4)}</td></tr>`).join("") +
          "</tbody></table></div>" : "");
    } catch (err) {
      $("#adminPayments").textContent = err.message;
    }
  }

  function setCycle(cycle) {
    state.cycle = cycle;
    $$("[data-cycle]").forEach((b) => b.classList.toggle("active", b.dataset.cycle === cycle));
    if (!state.config) return;
    const { monthly, annual } = state.config.prices_inr;
    $("#proPriceValue").innerHTML = cycle === "monthly"
      ? `${inr(monthly)} <span>/ month</span>`
      : `${inr(annual / 12)} <span>/ month, ${esc(inr(annual))} a year</span>`;
    $("#payAmount").textContent = cycle === "monthly" ? inr(monthly) : inr(annual);
    const links = state.config.upi_links;
    $("#payReady").classList.toggle("hidden", !links);
    $("#payNotReady").classList.toggle("hidden", !!links);
    $("#paymentForm").classList.toggle("hidden", !links);
    if (links) {
      $("#payQr").src = `/api/billing/upi-qr.svg?cycle=${cycle}`;
      $("#payLink").href = links[cycle];
    }
  }

  function openModal(id) {
    if (!state.user) return showPage("signup");
    const modal = $("#" + id);
    showError(modal, null);
    showOk(modal, "");
    modal.classList.add("open");
    const first = $("input", modal);
    if (first) setTimeout(() => first.focus(), 50);
  }

  function closeModals() { $$(".qr-modal-overlay").forEach((m) => m.classList.remove("open")); }

  function applyConfig(cfg) {
    state.config = cfg;
    const p = cfg.plans;
    const set = (key, text) => { const el = $(`[data-plan="${key}"]`); if (el) el.textContent = text; };
    set("free-datasets", `${p.free.max_datasets} datasets`);
    set("free-rows", `${num(p.free.max_rows)} shipments per dataset`);
    set("free-messages", cfg.llm_enabled ? `${p.free.daily_messages} Claude questions a day` : "AI assistant (quick answers)");
    set("pro-datasets", `${p.pro.max_datasets} datasets`);
    set("pro-rows", `${num(p.pro.max_rows)} shipments per dataset`);
    set("pro-messages", cfg.llm_enabled ? `${p.pro.daily_messages} Claude questions a day` : "AI assistant (quick answers)");
    $("#uploadMb").textContent = cfg.max_upload_mb;
    $("#payUpi").textContent = cfg.upi_id ? ` · UPI ID ${cfg.upi_id}` : "";
    const contact = $("#enterpriseContact");
    if (cfg.support_email) {
      contact.href = "mailto:" + encodeURIComponent(cfg.support_email).replace("%40", "@") + "?subject=Jenius%20Enterprise";
      $("#privacyContact").textContent = `Questions about your data? Email ${cfg.support_email}.`;
    } else {
      contact.classList.add("hidden");
    }
    setCycle(state.cycle);
  }

  // ── events ───────────────────────────────────────────────────────────────

  function bindForms() {
    $("#loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      showError(form.parentElement, null);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          const user = await api("/api/auth/login", { method: "POST", json: { email: form.email.value.trim(), password: form.password.value } });
          form.reset();
          await afterAuth(user);
        } catch (err) { showError(form.parentElement, err); }
      });
    });

    $("#signupForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      showError(form.parentElement, null);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          const user = await api("/api/auth/signup", { method: "POST", json: {
            name: form.name.value.trim(), email: form.email.value.trim(), password: form.password.value } });
          form.reset();
          await afterAuth(user);
        } catch (err) { showError(form.parentElement, err); }
      });
    });

    $("#resetRequestForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      showError(form.parentElement, null);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          const res = await api("/api/auth/password-reset/request", { method: "POST", json: { email: form.email.value.trim() } });
          showOk(form.parentElement, res.message);
        } catch (err) { showError(form.parentElement, err); }
      });
    });

    $("#resetConfirmForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      showError(form.parentElement, null);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          const user = await api("/api/auth/password-reset/confirm", { method: "POST", json: { token: state.resetToken, password: form.password.value } });
          state.resetToken = null;
          form.reset();
          await afterAuth(user);
        } catch (err) { showError(form.parentElement, err); }
      });
    });

    $("#chatForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("#chatInput");
      const q = input.value;
      input.value = "";
      ask(q);
    });

    $("#uploadForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const modal = $("#uploadModal");
      showError(modal, null);
      const data = new FormData();
      data.append("name", form.name.value.trim());
      data.append("shipments", form.shipments.files[0]);
      if (form.notes.files[0]) data.append("notes", form.notes.files[0]);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          const dataset = await api("/api/datasets", { method: "POST", form: data });
          form.reset();
          closeModals();
          state.dataset = { id: dataset.id };
          await loadDashboard();
          state.conversationId = null;
          renderChatIntro(`Analysis done: ${num(dataset.anomaly_count)} anomalous route-weeks found.`);
        } catch (err) { showError(modal, err); }
      });
    });

    $("#paymentForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const modal = $("#paymentModal");
      showError(modal, null);
      await withBusy($("button[type=submit]", form), async () => {
        try {
          await api("/api/billing/upi-claims", { method: "POST", json: { cycle: state.cycle, upi_reference: form.upi_reference.value.trim() } });
          form.reset();
          showOk(modal, "Thanks! We'll switch on Pro as soon as we've matched your payment. You can track it on your account page.");
        } catch (err) { showError(modal, err); }
      });
    });

    $("#deleteAccountForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      if (!confirm("Delete your account and all your data permanently?")) return;
      showError(form.parentElement, null);
      try {
        await api("/api/auth/account", { method: "DELETE", json: { password: form.password.value } });
        form.reset();
        setUser(null);
        showPage("landing");
      } catch (err) { showError(form.parentElement, err); }
    });

    $("#datasetSelect").addEventListener("change", (e) => selectDataset(e.target.value));
    $("#anomalyFilter").addEventListener("change", loadAnomalies);
  }

  const ACTIONS = {
    "open-upload": () => openModal("uploadModal"),
    "open-payment": () => openModal("paymentModal"),
    "close-modal": closeModals,
    "new-chat": () => { state.conversationId = null; renderChatIntro(); },
    "export-csv": () => { if (state.dataset) location.href = `/api/datasets/${encodeURIComponent(state.dataset.id)}/anomalies.csv`; },
    "delete-dataset": async () => {
      if (!state.dataset || !confirm(`Delete "${state.dataset.name}" and its chats?`)) return;
      try {
        await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}`, { method: "DELETE" });
        state.dataset = null;
        await loadDashboard();
      } catch (err) { alert(err.message); }
    },
    logout: async () => {
      try { await api("/api/auth/logout", { method: "POST", json: {} }); } catch { /* signed out anyway */ }
      setUser(null);
      state.dataset = null;
      state.conversationId = null;
      showPage("landing");
    },
  };

  document.addEventListener("click", async (e) => {
    const target = e.target.closest("[data-page],[data-action],[data-ask],[data-cycle],[data-review],[data-pay],[data-anomaly]");
    if (!target) return;
    if (target.dataset.page) { e.preventDefault(); closeModals(); return showPage(target.dataset.page); }
    if (target.dataset.action) { e.preventDefault(); return ACTIONS[target.dataset.action]?.(); }
    if (target.dataset.ask) return ask(target.dataset.ask);
    if (target.dataset.cycle) return setCycle(target.dataset.cycle);
    if (target.dataset.review) {
      e.stopPropagation();
      try {
        await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/anomalies/${encodeURIComponent(target.dataset.id)}`,
          { method: "PATCH", json: { review_status: target.dataset.review } });
        loadAnomalies();
      } catch (err) { alert(err.message); }
      return;
    }
    if (target.dataset.pay) {
      const note = target.dataset.pay === "reject" ? (prompt("Reason (shown to the customer):", "Payment not found") ?? null) : "";
      if (note === null) return;
      try {
        await api(`/api/admin/payments/${encodeURIComponent(target.dataset.id)}/${target.dataset.pay}`, { method: "POST", json: { note } });
        loadAdmin();
      } catch (err) { alert(err.message); }
      return;
    }
    if (target.dataset.anomaly) {
      ask(`Explain the ${target.dataset.route} anomaly in the week of ${target.dataset.week}. Is it justified?`);
    }
  });

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });
  $$(".qr-modal-overlay").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
  window.addEventListener("popstate", () => { closeModals(); showPage(pageFromHash(), { push: false }); });

  // ── hero typewriter ──────────────────────────────────────────────────────

  function typewriter() {
    const words = ["Intelligence", "Freight Audits", "Logistics", "Cost Control"];
    const el = $("#typewriterWord");
    let w = 0, c = 0, deleting = false;
    (function tick() {
      const word = words[w];
      c += deleting ? -1 : 1;
      el.textContent = word.substring(0, c);
      let delay = deleting ? 40 : 90;
      if (!deleting && c === word.length) { delay = 2200; deleting = true; }
      else if (deleting && c === 0) { deleting = false; w = (w + 1) % words.length; delay = 300; }
      setTimeout(tick, delay);
    })();
  }

  // ── start ────────────────────────────────────────────────────────────────

  async function start() {
    bindForms();
    typewriter();
    try { applyConfig(await api("/api/config")); } catch { /* the page still works without it */ }
    await refreshUser();
    showPage(pageFromHash(), { push: false });
  }

  start();
})();
