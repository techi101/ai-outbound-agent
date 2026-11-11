/* Outbound Agent UI. Plain JavaScript, no build step.
 * Four steps: 1 Describe -> 2 Research -> 3 Review prospects -> 4 Approve drafts.
 * Shows only what a run contains: the recorded example JSON or a live run
 * from this browser session. Nothing is ever sent. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const ICONS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    page: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
    gauge: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/>',
    save: '<path d="M6 3h12v18l-6-4-6 4z"/><path d="M9 10l2 2 4-4"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    check: '<path d="M5 12l5 5 9-10"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    replay: '<path d="M4 4v6h6"/><path d="M4.5 15a8 8 0 1 0 1.9-8.3L4 10"/>',
    shield: '<path d="M12 3l7 3v6c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  };
  const icon = (name, cls = "ico") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

  const TOOLS = {
    search_companies: { label: "Searched the web", icon: "search" },
    fetch_company_page: { label: "Read a company page", icon: "page" },
    extract_signals: { label: "Pulled facts from the page", icon: "list" },
    score_fit: { label: "Scored fit", icon: "gauge" },
    save_prospect: { label: "Saved as a prospect", icon: "save" },
    draft_outreach: { label: "Drafted an email", icon: "mail" },
  };

  const STEPS = [
    { n: 1, title: "Describe customer", short: "Describe", sub: "Who to reach" },
    { n: 2, title: "Agent research", short: "Research", sub: "Live step list" },
    { n: 3, title: "Review prospects", short: "Prospects", sub: "Fit scores" },
    { n: 4, title: "Approve drafts", short: "Approve", sub: "Critic checked" },
  ];

  const state = {
    config: null,
    example: null,
    runKey: "example",          // "example" or a live run id
    live: {},                   // run id -> latest view from the server
    mode: "example",            // step 1 tab: example | new
    form: { provider: "groq", api_key: "", model: "", icp: "", target: 2, min_score: 60 },
    replay: { shown: Infinity, timer: null, done: {} },
    edits: {},                  // runKey|company -> {subject, body}
    approved: load("approved", {}),
    drawer: null,               // index of open prospect
  };

  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem("oa:" + key)) ?? fallback; } catch { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem("oa:" + key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  }

  const run = () => (state.runKey === "example" ? state.example : state.live[state.runKey]);
  const isRecorded = () => state.runKey === "example";
  const pkey = (p) => `${state.runKey}|${p.company}`;
  const draftOf = (p) => state.edits[pkey(p)] || { subject: p.subject, body: p.body };
  const domainOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url || ""; } };
  const runLabel = () => (isRecorded() ? run().recorded_label : "Live run, this browser");

  // ---------------------------------------------------------------- routing

  function route() {
    const [, step = "1", idx] = (location.hash || "#/1").split("/");
    return { step: Math.min(Math.max(parseInt(step, 10) || 1, 1), 4), idx: idx === undefined ? null : parseInt(idx, 10) };
  }
  const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };

  function render() {
    const r = route();
    const data = run();
    let step = r.step;
    if (step === 4) step = 3;
    renderSidebar(r.step === 4 || r.idx !== null ? 4 : r.step);
    $("#run-pill").textContent = data ? runLabel() : "";
    $("#crumb").innerHTML = `Step ${r.step === 4 || r.idx !== null ? 4 : step} of 4 · <strong>${esc(STEPS[(r.idx !== null ? 4 : r.step) - 1].title)}</strong>`;
    const view = $("#view");
    if (step === 1) view.innerHTML = viewDescribe();
    if (step === 2) view.innerHTML = viewResearch();
    if (step === 3) view.innerHTML = viewProspects();
    bind(step);
    if (step === 2) startReplayIfNeeded();
    const prospects = (data && data.prospects) || [];
    if (r.step === 4 && r.idx === null) {
      const next = prospects.findIndex((p) => !state.approved[pkey(p)]);
      return go(`#/3/${next === -1 ? 0 : next}`);
    }
    if (r.idx !== null && prospects[r.idx]) openDrawer(r.idx); else closeDrawer(false);
  }

  function renderSidebar(active) {
    const data = run();
    const prospects = (data && data.prospects) || [];
    const approvedCount = prospects.filter((p) => state.approved[pkey(p)]).length;
    // A step shows a tick once you have moved past it, or when every draft is approved.
    const done = {
      1: !!data && active > 1,
      2: !!data && active > 2 && (isRecorded() || data.status === "done"),
      3: prospects.length > 0 && active > 3,
      4: prospects.length > 0 && approvedCount === prospects.length,
    };
    $("#steps").innerHTML = STEPS.map((s) => {
      let sub = s.sub;
      if (s.n === 3 && prospects.length) sub = `${prospects.length} found`;
      if (s.n === 4 && prospects.length) sub = `${approvedCount} of ${prospects.length} approved`;
      return `<button class="step ${done[s.n] && s.n !== active ? "done" : ""}" data-step="${s.n}" ${s.n === active ? 'aria-current="step"' : ""}>
        <span class="step-num">${done[s.n] && s.n !== active ? icon("check", "ico") : s.n}</span>
        <span class="step-text"><span class="step-title" data-short="${esc(s.short)}">${esc(window.innerWidth <= 860 ? s.short : s.title)}</span><span class="step-sub">${esc(sub)}</span></span>
      </button>`;
    }).join("");
    $("#steps").onclick = (e) => {
      const b = e.target.closest("[data-step]");
      if (b) go(`#/${b.dataset.step}`);
    };

    const items = [`<button class="run-item ${isRecorded() ? "active" : ""}" data-run="example">
      <span class="run-item-title">Recorded example</span>
      <span class="run-item-sub">2 Sep 2026 · ${state.example ? state.example.prospects.length : 0} prospects · no key needed</span></button>`];
    for (const [id, v] of Object.entries(state.live).reverse()) {
      const sub = v.status === "running" ? "Researching..." : v.status === "failed" ? "Failed" : `${v.prospects.length} prospects`;
      items.push(`<button class="run-item ${state.runKey === id ? "active" : ""}" data-run="${esc(id)}">
        <span class="run-item-title">${esc(v.icp)}</span><span class="run-item-sub">Live run · ${esc(sub)}</span></button>`);
    }
    $("#run-list").innerHTML = items.join("");
    $("#run-list").onclick = (e) => {
      const b = e.target.closest("[data-run]");
      if (!b) return;
      state.runKey = b.dataset.run;
      if (state.runKey === "example") state.mode = "example";
      go(state.runKey === "example" ? "#/1" : "#/2");
    };
  }

  function pageHead(n, title, lede, actions = "") {
    return `<div class="page-head"><div><p class="eyebrow">Step ${n} of 4</p><h1>${title}</h1><p class="lede">${lede}</p></div>
      <div class="head-actions">${actions}</div></div>`;
  }

  // ---------------------------------------------------------------- step 1

  function viewDescribe() {
    const ex = state.example;
    const recorded = state.mode === "example";
    const f = state.form;
    const providers = state.config ? state.config.providers : { groq: "Groq", gemini: "Gemini", claude: "Claude" };
    const missing = !f.api_key.trim() ? "Add your API key to start." : !f.icp.trim() ? "Describe your ideal customer to start." : "";
    const busy = Object.values(state.live).some((v) => v.status === "running");

    const body = recorded
      ? `<div class="form-grid">
          <div class="field full"><label for="icp-ro">Ideal customer profile</label>
            <textarea id="icp-ro" readonly>${esc(ex.icp)}</textarea></div>
          <div class="field"><label for="t-ro">Prospects to find</label><input id="t-ro" readonly value="${ex.target}"></div>
          <div class="field"><label for="m-ro">Minimum fit score (0 to 100)</label><input id="m-ro" readonly value="${ex.min_score}"></div>
        </div>`
      : `<div class="form-grid">
          <div class="field full"><label for="icp">Ideal customer profile</label>
            <textarea id="icp" placeholder="e.g. seed-stage Indian SaaS companies hiring their first AI engineer">${esc(f.icp)}</textarea>
            <span class="hint">One or two sentences: industry, stage, location and a buying signal.</span></div>
          <div class="field"><label for="target">Prospects to find</label><input id="target" type="number" min="1" max="10" value="${f.target}"></div>
          <div class="field"><label for="min_score">Minimum fit score (0 to 100)</label><input id="min_score" type="number" min="0" max="100" value="${f.min_score}"></div>
          <div class="field"><label for="provider">Model provider</label>
            <select id="provider">${Object.entries(providers).map(([k, v]) => `<option value="${k}" ${k === f.provider ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
          <div class="field"><label for="model">Model</label><input id="model" value="${esc(f.model || (state.config ? state.config.default_models[f.provider] : ""))}"></div>
          <div class="field full"><label for="api_key">Your API key</label><input id="api_key" type="password" autocomplete="off" value="${esc(f.api_key)}" placeholder="Paste a ${esc(providers[f.provider])} key">
            <span class="hint">Used for this run only. Not saved to disk or to the database.</span></div>
        </div>`;

    const foot = recorded
      ? `<span class="hint">This is the profile from the recorded run. No API key needed. For your own search, pick <strong>New search</strong>.</span>
         <button class="btn btn-primary btn-lg" id="primary">See how the agent researched it ${icon("arrow")}</button>`
      : `<span class="hint" id="missing">${busy ? "A run is already in progress." : esc(missing) || "Takes a few minutes. Keep this tab open to watch every step."}</span>
         <button class="btn btn-primary btn-lg" id="primary" ${missing || busy ? "disabled" : ""}>Start research ${icon("arrow")}</button>`;

    return `${pageHead(1, "Describe your ideal customer", "Write who you want to reach. The agent finds matching companies, reads their sites, scores each one and drafts an email you can approve.")}
      <div class="seg" role="group" aria-label="Choose run">
        <button data-mode="example" aria-pressed="${recorded}">Recorded example</button>
        <button data-mode="new" aria-pressed="${!recorded}">New search</button>
      </div>
      <section class="card">
        <div class="card-body">${body}</div>
        <div class="form-foot">${foot}</div>
      </section>
      <p class="how-label">What happens next</p>
      <div class="how">
        ${how("search", "1. Search", "Searches the web for companies that match, from several angles.")}
        ${how("page", "2. Read", "Opens each company's own site, usually the careers or about page.")}
        ${how("gauge", "3. Score", "Pulls out facts and gives a fit score from 0 to 100. Low scores are dropped.")}
        ${how("shield", "4. Draft and check", "Writes an email. A second AI check rejects anything generic.")}
      </div>`;
  }
  const how = (ic, t, d) => `<div class="how-item"><div class="how-icon">${icon(ic)}</div><h3>${t}</h3><p>${d}</p></div>`;

  // A live run is one streaming request: each line is a JSON event.
  async function startLive() {
    const f = state.form;
    const btn = $("#primary");
    btn.disabled = true;
    let res;
    try {
      res = await fetch("api/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...f, target: +f.target, min_score: +f.min_score }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Could not start the run.");
      }
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
      return;
    }
    f.api_key = "";
    const id = "live-" + Date.now();
    const v = { status: "running", icp: f.icp, steps: [], reviews: [], prospects: [], model_switches: [], skipped: [],
      min_score: +f.min_score, target: +f.target, provider: f.provider, model: f.model || (state.config ? state.config.default_models[f.provider] : "") };
    state.live[id] = v;
    state.runKey = id;
    go("#/2");
    const refresh = () => { if (state.runKey === id && route().step === 2) render(); else renderSidebar(route().idx !== null ? 4 : route().step); };
    const handle = (e) => {
      if (e.type === "step") v.steps.push({ tool: e.tool, detail: e.detail, ok: e.ok });
      else if (e.type === "review") v.reviews.push(e);
      else if (e.type === "done") { Object.assign(v, e, { status: "done" }); delete v.type; }
      else if (e.type === "error") { v.status = "failed"; v.error = e.error; }
      else return;
      refresh();
    };
    try {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) !== -1) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (line) handle(JSON.parse(line));
        }
      }
    } catch { /* connection dropped */ }
    if (v.status === "running") { v.status = "failed"; v.error = "The connection closed before the run finished. Try again."; refresh(); }
    toast(v.status === "done" ? `Research finished: ${v.prospects.length} prospects.` : "The run failed. See step 2.");
  }

  // ---------------------------------------------------------------- step 2

  function viewResearch() {
    const data = run();
    const steps = data.steps || [];
    const live = !isRecorded();
    const running = live && data.status === "running";
    const shown = isRecorded() ? Math.min(state.replay.shown, steps.length) : steps.length;
    const replaying = isRecorded() && shown < steps.length;
    const n = data.prospects.length;

    if (live && data.status === "failed") {
      return `${pageHead(2, "Agent research", "The run stopped before it finished.")}
        <div class="notice notice-bad">${icon("alert")}<div><strong>Run failed.</strong> ${esc(data.error)}</div></div>
        <button class="btn btn-primary" id="back1">Back to step 1</button>`;
    }

    const lede = isRecorded()
      ? `Every tool call the agent made on 2 Sep 2026, in order. It picked each next step itself.`
      : running ? "The agent is working. New steps appear as it makes them." : "Every tool call the agent made, in order.";
    const primaryLabel = running ? `Researching...` : n ? `Review ${n} prospect${n === 1 ? "" : "s"}` : "No prospects saved";
    const actions = `${isRecorded() ? `<button class="btn" id="replay">${icon("replay")} Replay</button>` : ""}
      <button class="btn btn-primary btn-lg" id="primary" ${running || replaying || !n ? "disabled" : ""}>${primaryLabel} ${running ? "" : icon("arrow")}</button>`;

    const rows = steps.slice(0, shown).map((s, i) => {
      const t = TOOLS[s.tool] || { label: s.tool, icon: "list" };
      const warn = s.note || s.ok === false;
      const cls = s.tool === "save_prospect" ? "save" : warn ? "warn" : "";
      const tag = s.note ? `<span class="pill pill-warn">${esc(s.note)}</span>`
        : s.ok === false ? `<span class="pill pill-bad">Tool error</span>`
        : s.tool === "save_prospect" ? `<span class="pill pill-ok">Saved</span>` : "";
      const enter = isRecorded() && state.replay.timer && i === shown - 1 ? " enter" : "";
      return `<li class="tl-item${enter}"><span class="tl-icon ${cls}">${icon(t.icon)}</span>
        <div class="tl-main"><div class="tl-title"><span class="tl-num">${i + 1}</span>${esc(t.label)}</div>
        <div class="tl-detail" title="${esc(s.detail)}">${esc(s.detail)}</div></div>${tag}</li>`;
    }).join("");
    const waiting = running ? `<li class="tl-wait"><span class="spinner"></span> ${data.reviews && data.reviews.length ? "Critic is reviewing drafts..." : "Thinking about the next step..."}</li>` : "";
    const empty = !steps.length && !running ? `<li class="tl-wait">No tool calls were recorded.</li>` : "";

    const searches = steps.filter((s) => s.tool === "search_companies").length;
    const pages = steps.filter((s) => s.tool === "fetch_company_page").length;
    const progress = steps.length ? Math.round((shown / steps.length) * 100) : 0;

    return `${pageHead(2, "Agent research", lede, actions)}
      <div class="split">
        <section class="card">
          <div class="card-head"><h2 class="card-title">Tool calls</h2>
            <span class="hint">${replaying ? `Showing step ${shown} of ${steps.length}` : `${steps.length} step${steps.length === 1 ? "" : "s"}${running ? " so far" : ""}`}</span></div>
          ${isRecorded() ? `<div style="padding:0 18px"><div class="progress"><span style="width:${progress}%"></span></div></div>` : ""}
          <ol class="timeline">${rows}${waiting}${empty}</ol>
        </section>
        <div class="stack">
          ${summaryCard(data, searches, pages)}
          ${costCard(data)}
          ${switchCard(data)}
        </div>
      </div>`;
  }

  function summaryCard(d, searches, pages) {
    const skipped = (d.skipped || []).map((s) => `${esc(s.company)} <small>${esc(s.why)}</small>`).join("");
    return `<section class="card"><div class="card-head"><h2 class="card-title">Run summary</h2></div><div class="card-body">
      <dl class="stats">
        <div class="stat"><dt>Web searches</dt><dd>${searches}</dd></div>
        <div class="stat"><dt>Company pages read</dt><dd>${pages}</dd></div>
        ${d.iterations ? `<div class="stat"><dt>Agent iterations</dt><dd>${d.iterations}</dd></div>` : ""}
        <div class="stat"><dt>Prospects saved</dt><dd>${d.prospects.length} <small>of ${d.target} wanted, fit score ${d.min_score}+</small></dd></div>
        ${skipped ? `<div class="stat"><dt>Checked and dropped</dt><dd>${skipped}</dd></div>` : ""}
      </dl></div></section>`;
  }

  function costCard(d) {
    if (d.input_tokens === undefined) return "";
    return `<section class="card"><div class="card-head"><h2 class="card-title">Cost</h2></div><div class="card-body">
      <dl class="stats">
        <div class="stat"><dt>Model</dt><dd>${esc(d.model || "")}</dd></div>
        <div class="stat"><dt>Tokens sent to model</dt><dd>${fmt(d.input_tokens)}</dd></div>
        <div class="stat"><dt>Tokens generated</dt><dd>${fmt(d.output_tokens)}</dd></div>
        <div class="stat"><dt>Cost of run</dt><dd>${costParts(d.cost_label)}</dd></div>
        ${d.cost_per_qualified_label ? `<div class="stat"><dt>Cost per qualified prospect</dt><dd>${esc(d.cost_per_qualified_label)}</dd></div>` : ""}
      </dl></div></section>`;
  }

  // "$0.00 (free tier; would list at $0.0595)" -> main figure plus a small note.
  function costParts(label) {
    const m = String(label || "").match(/^(\S+)\s*\((.*)\)$/);
    return m ? `${esc(m[1])}<small>${esc(m[2])}</small>` : esc(label);
  }

  function switchCard(d) {
    const sw = d.model_switches || [];
    if (!sw.length) return "";
    return `<section class="card"><div class="card-body"><details class="fold"><summary><span><strong>Model fallbacks: ${sw.length}</strong><br>
      <span class="hint">A model was unavailable, so the agent switched and kept going.</span></span></summary>
      <ul class="switch-list">${sw.map((s) => `<li>${esc(s.from)} → ${esc(s.to)} <em>(${esc(s.why)})</em></li>`).join("")}</ul>
      </details></div></section>`;
  }

  function startReplayIfNeeded() {
    if (!isRecorded() || state.replay.done.example) return;
    state.replay.done.example = true;
    playReplay();
  }
  function playReplay() {
    const total = state.example.steps.length;
    clearInterval(state.replay.timer);
    if (reduceMotion) { state.replay.shown = total; state.replay.timer = null; return render(); }
    state.replay.shown = 1;
    state.replay.timer = setInterval(() => {
      state.replay.shown += 1;
      if (state.replay.shown >= total) { clearInterval(state.replay.timer); state.replay.timer = null; }
      if (route().step === 2 && isRecorded()) render();
    }, 220);
    render();
  }

  // ---------------------------------------------------------------- step 3

  function viewProspects() {
    const d = run();
    const ps = d.prospects || [];
    if (!ps.length) {
      const msg = !isRecorded() && d.status === "running" ? "The agent is still researching. Prospects appear here when it finishes." : "This run saved no prospects.";
      return `${pageHead(3, "Review prospects", "Companies that cleared your minimum fit score.")}
        <section class="card"><div class="empty"><h3>No prospects yet</h3><p>${msg}</p>
        <button class="btn btn-primary" id="to2">Go to step 2</button></div></section>`;
    }
    const top = ps.reduce((best, p, i) => (p.score > ps[best].score ? i : best), 0);
    const actions = `<button class="btn btn-primary btn-lg" id="primary">Open top draft ${icon("arrow")}</button>`;
    const rows = ps.map((p, i) => {
      const approved = state.approved[pkey(p)];
      const verdict = p.critic_verdict === "pass"
        ? `<span class="pill pill-ok">${icon("check")} Passed${p.revisions ? ` after ${p.revisions} rewrite${p.revisions === 1 ? "" : "s"}` : ""}</span>`
        : p.critic_verdict ? `<span class="pill pill-bad">${icon("x")} Failed</span>` : `<span class="pill pill-muted">Not reviewed</span>`;
      const status = !p.body ? `<span class="pill pill-muted">No draft</span>`
        : approved ? `<span class="pill pill-brand">Approved</span>` : `<span class="pill pill-muted">Needs review</span>`;
      return `<tr data-idx="${i}" tabindex="0" aria-label="Open draft for ${esc(p.company)}">
        <td data-label="Company"><div class="co">${logo(p)}<div><div class="co-name">${esc(p.company)}</div><div class="co-domain">${esc(domainOf(p.url))}</div></div></div></td>
        <td data-label="Fit score">${score(p.score)}</td>
        <td data-label="Signals found"><div class="chips">${(p.signal_chips || []).map((c) => `<span class="chip" title="${esc(c)}">${esc(c)}</span>`).join("")}</div></td>
        <td data-label="Critic review">${verdict}</td>
        <td data-label="Draft">${status}</td>
        <td class="row-go">${icon("chevron")}</td></tr>`;
    }).join("");
    return `${pageHead(3, "Review prospects", `${ps.length} compan${ps.length === 1 ? "y" : "ies"} cleared the minimum fit score of ${d.min_score}. Click a row to read its draft email.`, actions)}
      <section class="card" data-top="${top}">
        <div class="toolbar"><strong>${ps.length} prospect${ps.length === 1 ? "" : "s"}</strong>
          <div class="legend hint"><span>${icon("info")} Fit score: 0 to 100, how well the company matches your profile</span></div></div>
        <table class="table"><thead><tr><th>Company</th><th>Fit score</th><th>Signals found</th><th>Critic review</th><th>Draft</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>
      </section>`;
  }

  const logo = (p, lg = false) => `<span class="logo ${lg ? "logo-lg" : ""}">${p.logo ? `<img src="${esc(p.logo)}" alt="">` : esc((p.company || "?")[0].toUpperCase())}</span>`;
  const score = (s) => `<span class="score"><span class="score-num">${s}<small> /100</small></span><span class="score-bar"><span style="width:${Math.max(0, Math.min(100, s))}%"></span></span></span>`;

  // ---------------------------------------------------------------- step 4 (drawer)

  function openDrawer(i) {
    const d = run();
    const p = d.prospects[i];
    state.drawer = i;
    const draft = draftOf(p);
    const approved = state.approved[pkey(p)];
    const words = (draft.body || "").trim().split(/\s+/).filter(Boolean).length;
    const passed = p.critic_verdict === "pass";
    const verdictText = passed
      ? p.revisions ? `Passed after ${p.revisions} rewrite${p.revisions === 1 ? "" : "s"}` : "Passed on the first attempt"
      : p.critic_verdict ? "Failed review" : "Not reviewed";

    const el = $("#drawer");
    el.innerHTML = `
      <div class="drawer-head">${logo(p, true)}
        <div class="grow"><p class="eyebrow" style="margin:0 0 2px">Step 4 of 4 · Approve draft</p><h2 class="drawer-title">${esc(p.company)}</h2>
          <div class="drawer-meta"><span class="pill pill-muted">Fit score ${p.score} /100</span>
          ${p.url ? `<a class="pill pill-muted" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(domainOf(p.url))} ${icon("ext")}</a>` : ""}</div></div>
        <button class="icon-btn" id="close" aria-label="Close">${icon("x")}</button>
      </div>
      <div class="drawer-body">
        <section><h3 class="sec-title">Draft email</h3>
          ${p.body ? `<div class="email">
            <div class="email-row"><label for="d-subject">Subject</label><input id="d-subject" value="${esc(draft.subject)}"></div>
            <textarea id="d-body" aria-label="Email body">${esc(draft.body)}</textarea>
            <div class="email-foot"><span id="wc">${words} words (critic limit: 120)</span><span>You can edit before approving</span></div>
          </div>` : `<p class="hint">No draft passed review for this company.</p>`}
        </section>
        <section><h3 class="sec-title">Critic review</h3>
          <div class="verdict ${passed ? "" : "fail"}">
            <div class="verdict-head">${icon(passed ? "check" : "x")} ${verdictText}</div>
            ${p.critic_note ? `<p>${esc(p.critic_note)}</p>` : ""}
            <p class="hint">A second AI call asks: would this email still make sense with another company's name in it? If yes, it is rejected and rewritten.</p>
          </div>
        </section>
        <section><h3 class="sec-title">Signals the agent found</h3><p class="signals-text">${esc(p.signals_text)}</p>
          ${p.reasoning ? `<h3 class="sec-title" style="margin-top:16px">Why this fit score</h3><p class="signals-text">${esc(p.reasoning)}</p>` : ""}
        </section>
      </div>
      <div class="drawer-foot">
        <span class="grow">${approved ? "Approved. Nothing was sent: copy it into your own email tool." : "Drafts only. This app never sends email."}</span>
        ${p.body ? `<button class="btn" id="copy">${icon("copy")} Copy email</button>
        <button class="btn btn-primary" id="approve">${approved ? `${icon("check")} Approved · Undo` : `${icon("check")} Approve draft`}</button>` : ""}
      </div>`;
    el.classList.add("open");
    el.setAttribute("aria-hidden", "false");
    $("#scrim").hidden = false;
    document.body.style.overflow = "hidden";

    $("#close").onclick = () => go("#/3");
    const fit = (t) => { if (t) { t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; } };
    fit($("#d-body"));
    const subj = $("#d-subject"), body = $("#d-body");
    if (body) {
      const sync = () => {
        state.edits[pkey(p)] = { subject: subj.value, body: body.value };
        fit(body);
        $("#wc").textContent = `${body.value.trim().split(/\s+/).filter(Boolean).length} words (critic limit: 120)`;
      };
      subj.oninput = sync; body.oninput = sync;
      $("#copy").onclick = async () => {
        const text = `Subject: ${subj.value}\n\n${body.value}`;
        try { await navigator.clipboard.writeText(text); toast("Email copied. Paste it into your own email tool."); }
        catch { body.select(); toast("Select-all done. Press Ctrl+C to copy."); }
      };
      $("#approve").onclick = () => {
        if (state.approved[pkey(p)]) delete state.approved[pkey(p)];
        else state.approved[pkey(p)] = true;
        save("approved", state.approved);
        const now = state.approved[pkey(p)];
        toast(now ? `${p.company} draft approved. Nothing was sent.` : "Approval removed.");
        const next = d.prospects.findIndex((q) => !state.approved[pkey(q)]);
        render();
        if (now && next !== -1 && next !== i) setTimeout(() => toast(`Next: ${d.prospects[next].company}`), 1800);
      };
    }
  }

  function closeDrawer() {
    const el = $("#drawer");
    if (!el.classList.contains("open")) return;
    el.classList.remove("open");
    el.setAttribute("aria-hidden", "true");
    $("#scrim").hidden = true;
    document.body.style.overflow = "";
    state.drawer = null;
  }

  // ---------------------------------------------------------------- binding

  function bind(step) {
    const primary = $("#primary");
    if (step === 1) {
      document.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { state.mode = b.dataset.mode; if (state.mode === "example") state.runKey = "example"; render(); }));
      if (state.mode === "example") primary.onclick = () => { state.runKey = "example"; go("#/2"); };
      else {
        const f = state.form;
        const refresh = () => {
          const missing = !f.api_key.trim() ? "Add your API key to start." : !f.icp.trim() ? "Describe your ideal customer to start." : "";
          const busy = Object.values(state.live).some((v) => v.status === "running");
          primary.disabled = !!missing || busy;
          $("#missing").textContent = busy ? "A run is already in progress." : missing || "Takes a few minutes. Keep this tab open to watch every step.";
        };
        for (const id of ["icp", "target", "min_score", "model", "api_key"]) {
          $("#" + id).oninput = (e) => { f[id] = e.target.value; refresh(); };
        }
        $("#provider").onchange = (e) => { f.provider = e.target.value; f.model = ""; f.api_key = ""; render(); };
        primary.onclick = startLive;
      }
    }
    if (step === 2) {
      if (primary) primary.onclick = () => go("#/3");
      const rp = $("#replay");
      if (rp) rp.onclick = playReplay;
      const b1 = $("#back1");
      if (b1) b1.onclick = () => { state.mode = "new"; go("#/1"); };
    }
    if (step === 3) {
      if (primary) primary.onclick = () => go(`#/3/${$("[data-top]").dataset.top}`);
      const t2 = $("#to2");
      if (t2) t2.onclick = () => go("#/2");
      const tbody = $(".table tbody");
      if (tbody) {
        tbody.onclick = (e) => { const tr = e.target.closest("tr"); if (tr) go(`#/3/${tr.dataset.idx}`); };
        tbody.onkeydown = (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr"); if (tr) go(`#/3/${tr.dataset.idx}`); } };
      }
    }
  }

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  $("#scrim").onclick = () => go("#/3");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.drawer !== null) go("#/3"); });
  window.addEventListener("hashchange", render);
  let lastNarrow = window.innerWidth <= 860;
  window.addEventListener("resize", () => { const n = window.innerWidth <= 860; if (n !== lastNarrow) { lastNarrow = n; renderSidebar(route().idx !== null ? 4 : route().step); } });

  async function boot() {
    const [ex, cfg] = await Promise.all([
      fetch("api/example").then((r) => r.json()),
      fetch("api/config").then((r) => r.json()).catch(() => null),
    ]);
    state.example = ex;
    state.config = cfg;
    render();
  }
  boot().catch(() => {
    $("#view").innerHTML = `<div class="notice notice-bad">${icon("alert")}<div>Could not load the recorded example. Is the server running?</div></div>`;
  });
})();
