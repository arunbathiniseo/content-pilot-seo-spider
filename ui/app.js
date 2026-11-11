/* Local SEO Spider UI */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  };
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtNum = (n) => (n ?? "").toLocaleString ? Number(n).toLocaleString() : n;

  // ------------------------------------------------------------------ loading primitives
  const skel = {
    rows: (n = 10) => `<div class="skeleton-rows">${Array.from({ length: n }, (_, i) => `<div class="skeleton-row"><span class="skeleton" style="width:${38 - (i % 3) * 6}%"></span><span class="skeleton" style="width:8%"></span><span class="skeleton" style="width:5%"></span><span class="skeleton" style="width:7%"></span><span class="skeleton" style="width:${22 + (i % 4) * 4}%"></span></div>`).join("")}</div>`,
    kv: (n = 8) => `<div class="skeleton-kv">${Array.from({ length: n }, (_, i) => `<span class="skeleton skeleton-line" style="width:${60 + (i % 3) * 15}px"></span><span class="skeleton skeleton-line" style="width:${30 + (i * 13) % 50}%"></span>`).join("")}</div>`,
    tabs: (n = 12) => `<div class="skeleton-tabs">${Array.from({ length: n }, () => `<span class="skeleton"></span>`).join("")}</div>`,
    list: (n = 14) => `<div class="skeleton-rows">${Array.from({ length: n }, (_, i) => `<div class="skeleton-row"><span class="skeleton" style="width:${50 + (i % 4) * 10}%"></span><span class="skeleton" style="width:8%;margin-left:auto"></span></div>`).join("")}</div>`,
    inline: (msg) => `<div class="loading-inline"><span class="spinner"></span>${esc(msg)}</div>`,
  };
  const SPIDER = `<svg viewBox="0 0 512 512" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="34" stroke-linecap="round" stroke-linejoin="round"><path d="M212 214 L150 158 L96 112"/><path d="M202 248 L116 232 L54 242"/><path d="M206 284 L136 322 L72 374"/><path d="M232 302 L198 366 L172 432"/><path d="M300 214 L362 158 L416 112"/><path d="M310 248 L396 232 L458 242"/><path d="M306 284 L376 322 L440 374"/><path d="M280 302 L314 366 L340 432"/></g><g fill="currentColor"><circle cx="96" cy="112" r="24"/><circle cx="54" cy="242" r="24"/><circle cx="72" cy="374" r="24"/><circle cx="172" cy="432" r="24"/><circle cx="416" cy="112" r="24"/><circle cx="458" cy="242" r="24"/><circle cx="440" cy="374" r="24"/><circle cx="340" cy="432" r="24"/></g><circle cx="256" cy="256" r="64" fill="var(--accent)"/></svg>`;
  const empty = (glyph, title, hint) => `<div class="empty-state"><div class="glyph">${glyph === "spider" ? SPIDER : (window.ICONS && window.ICONS[glyph]) ? ic(glyph, 30) : esc(glyph)}</div><b>${esc(title)}</b><span>${esc(hint || "")}</span></div>`;
  const splash = {
    show: (msg) => { $("#splash-msg").textContent = msg || ""; $("#splash").classList.remove("hide"); },
    hide: () => $("#splash").classList.add("hide"),
  };
  function toast(msg, kind = "info", ms = 4500) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`; el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 250); }, ms);
  }
  const busy = async (btn, fn) => { btn.classList.add("busy"); btn.disabled = true; try { return await fn(); } finally { btn.classList.remove("busy"); btn.disabled = false; } };

  const S = {
    tabs: [], columns: {}, tab: "url", filter: "url:all", q: "", colFilters: {},
    status: { state: "idle" }, table: null, selectedId: null, detail: null, bottomPane: "details", sidePane: "overview",
    config: {}, defaults: {}, userAgents: {}, listUrls: [], aiCatalog: {}, fixRows: [],
  };

  // ------------------------------------------------------------------ tabs & filters
  async function loadTabs() {
    const d = await api("/api/tabs");
    S.tabs = d.tabs; S.columns = d.columns;
    renderTabs();
    renderFilterSelect();
    renderIssueBar();
    if (S.sidePane === "overview") renderSide();
  }

  function renderTabs() {
    const nav = $("#tabs");
    nav.innerHTML = S.tabs.map((t) => {
      const all = t.filters.find((f) => f.key.endsWith(":all")) || t.filters[0];
      const n = all ? all.count : 0;
      return `<button data-tab="${t.key}" class="${t.key === S.tab ? "active" : ""}">${esc(t.name)}<span class="n">${n ? fmtNum(n) : ""}</span></button>`;
    }).join("");
    $$("button", nav).forEach((b) => b.onclick = () => selectTab(b.dataset.tab));
  }

  function renderFilterSelect() {
    const t = S.tabs.find((x) => x.key === S.tab);
    const sel = $("#filter");
    if (!t) { sel.innerHTML = ""; return; }
    sel.innerHTML = t.filters.filter((f) => !f.issue_only).map((f) => `<option value="${f.key}">${esc(f.name)} (${fmtNum(f.count)})</option>`).join("");
    if (!t.filters.some((f) => f.key === S.filter)) S.filter = t.filters[0].key;
    sel.value = S.filter;
    const f = t.filters.find((x) => x.key === S.filter);
    $("#filter-desc").textContent = f && f.desc && !f.kind ? f.desc : "";   // issue filters get the detail bar instead
  }

  function currentFilter() {
    const t = S.tabs.find((x) => x.key === S.tab);
    return t ? t.filters.find((f) => f.key === S.filter) : null;
  }
  function renderIssueBar() {
    const f = currentFilter(), bar = $("#issue-bar");
    if (!f || !f.kind || S.issueBarDismissed === f.key) { bar.classList.add("hidden"); return; }
    const tabName = (S.tabs.find((x) => x.key === S.tab) || {}).name || "";
    bar.className = f.kind;
    bar.innerHTML = `<div class="col"><div class="t"><span class="badge ${f.kind}" title="${f.kind}"></span>${esc(tabName)}: ${esc(f.name)}</div><div class="meta">${esc(f.kind)} · ${esc(f.priority || "")} priority · ${fmtNum(f.count)} URL${f.count === 1 ? "" : "s"} affected</div></div>
      <div class="col"><b>What it means</b>${esc(f.desc || "No description.")}</div>
      <div class="fix"><b>How to fix</b>${esc(f.fix || "Review the listed URLs.")}</div>
      <div class="acts"><a class="btn" href="/api/export/issue?key=${encodeURIComponent(f.key)}&what=urls&format=xlsx" title="These URLs with their columns">${ic("download")}URLs</a> <a class="btn" href="/api/export/issue?key=${encodeURIComponent(f.key)}&what=inlinks&format=xlsx" title="Every link pointing at these URLs — for fixing links">${ic("download")}Inlinks</a></div>
      <button class="close" title="Hide this panel" aria-label="Hide">${ic("x")}</button>`;
    $(".close", bar).onclick = () => { S.issueBarDismissed = f.key; bar.classList.add("hidden"); };
    bar.classList.remove("hidden");
  }
  function syncSideHighlight() {
    $$("#side-body .issue-row").forEach((r) => r.classList.toggle("active", r.dataset.filter === S.filter));
    $$("#side-body .ov-filter").forEach((r) => r.classList.toggle("active", r.dataset.filter === S.filter));
    const a = $("#side-body .issue-row.active, #side-body .ov-filter.active"); if (a) a.scrollIntoView({ block: "nearest" });
  }
  function selectTab(key, filter) {
    if (key === "url" && !filter) { filter = "url:all"; S.q = ""; $("#search").value = ""; S.colFilters = {}; }   // All URLs = reset view
    S.tab = key;
    const t = S.tabs.find((x) => x.key === key);
    S.filter = filter || (t && t.filters[0] ? t.filters[0].key : `${key}:all`);
    S.issueBarDismissed = null;
    renderTabs();
    renderFilterSelect();
    renderIssueBar();
    buildTable();
    syncSideHighlight();
  }

  $("#filter").onchange = (e) => { S.filter = e.target.value; S.issueBarDismissed = null; renderFilterSelect(); renderIssueBar(); buildTable(); syncSideHighlight(); };
  let searchTimer;
  $("#search").oninput = (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { S.q = e.target.value.trim(); S.table && S.table.setData(); }, 300); };

  // ------------------------------------------------------------------ grid
  function statusClass(v) {
    if (v == null || v === "") return "st-0";
    if (v >= 500) return "st-5xx"; if (v >= 400) return "st-4xx"; if (v >= 300) return "st-3xx"; return "st-200";
  }

  // Tabulator puts a tooltip string into innerHTML; crawled text must go in as text
  function textTip(e, cell) { const v = cell.getValue(); if (v === null || v === undefined || v === "") return ""; const d = document.createElement("div"); d.textContent = String(v); return d; }

  function buildTable() {
    const cols = (S.columns[S.tab] || S.columns.internal).map((c) => {
      const col = { title: c.title, field: c.field, headerSort: true, tooltip: textTip };
      if (c.field === "url") { col.width = 420; col.frozen = true; }
      else if (["title", "meta_desc", "h1", "h2", "canonical", "redirect_url", "final_url", "hreflang", "custom", "sd_types", "amp_issues", "amp_url"].includes(c.field)) col.width = 260;
      else if (["content_type", "indexability_status", "status", "type", "readability", "closest_match", "meta_robots", "x_robots", "redirect_type", "lang"].includes(c.field)) col.width = 150;
      else col.width = Math.max(78, Math.min(150, c.title.length * 7 + 26));   // fixed widths: no per-cell measuring on scroll (fitToData)
      if (c.field === "status_code") col.formatter = (cell) => `<span class="${statusClass(cell.getValue())}">${cell.getValue() ?? ""}</span>`;
      col.width = Math.max(col.width, 104);                 // room for the filter box + funnel
      col.headerFilter = headerFilterEl; col.headerFilterLiveFilter = false;   // filtering is ours, server side
      return col;
    });
    const fields = new Set(cols.map((c) => c.field));       // keep filters on columns this tab also has
    Object.keys(S.colFilters).forEach((k) => { if (!fields.has(k)) delete S.colFilters[k]; });
    updateColFilterBar();
    if (S.table) { S.table.destroy(); S.table = null; }
    const placeholder = S.status.state === "idle" && !S.tabs.some((t) => t.filters.some((f) => f.count))
      ? empty("spider", "No crawl open", "Enter a URL above and press Start, or open a saved crawl from Crawls.")
      : empty("check", "No URLs match this filter", "Nothing to fix here. Pick another filter or tab.");
    S.table = new Tabulator("#grid", {
      height: "100%", layout: "fitData", renderHorizontal: "virtual", renderVerticalBuffer: 400, columns: cols, selectableRows: true, selectableRowsRangeMode: "click", placeholder,
      dataLoader: true, dataLoaderLoading: skel.rows(12), dataLoaderError: empty("!", "Could not load rows", "Check that the app server is still running."),
      pagination: true, paginationMode: "remote", paginationSize: 200, paginationSizeSelector: [100, 200, 500, 1000],
      sortMode: "remote", ajaxURL: "/api/urls", ajaxConfig: "GET",
      ajaxURLGenerator: (url, config, params) => {
        const sort = params.sort && params.sort[0] ? `&sort=${encodeURIComponent(params.sort[0].field)}&dir=${params.sort[0].dir}` : "";
        return `${url}?key=${encodeURIComponent(S.filter)}&page=${params.page || 1}&size=${params.size || 200}&q=${encodeURIComponent(S.q)}${sort}${filtersQS()}`;
      },
      ajaxResponse: (url, params, resp) => { $("#rowcount").textContent = `${fmtNum(resp.total)} URLs${colFilterList().length ? " (filtered)" : ""}`; return resp; },
      ajaxError: () => { $("#rowcount").textContent = ""; },
    });
    S.table.on("rowClick", (e, row) => showDetail(row.getData().id));
    S.table.on("rowDblClick", (e, row) => { const u = row.getData().url; if (u) window.open(u, "_blank", "noopener"); });
    S.table.on("rowSelectionChanged", (data) => {
      const n = data.length;
      $("#selection").classList.toggle("hidden", n < 2);
      $("#selcount").textContent = fmtNum(n);
    });
    S.table.on("dataLoading", () => { S.tableLoading = true; });
    S.table.on("dataLoaded", () => { S.tableLoading = false; });
    S.table.on("dataLoadError", () => { S.tableLoading = false; });
  }

  // ------------------------------------------------------------------ column filters (Excel / Google Sheets style)
  // S.colFilters[field] = { cond: {op, value, value2} | null, vals: {mode: "in" | "not_in", list: [..]} | null }.
  // Typing in the header box = "contains"; the funnel opens condition + "filter by values". Applied server side, so they
  // work across every page of results and carry into Export.
  const COND_OPS = [["none", "None"], ["contains", "Contains"], ["not_contains", "Does not contain"], ["equals", "Is equal to"],
    ["not_equals", "Is not equal to"], ["starts", "Starts with"], ["ends", "Ends with"], ["empty", "Is empty"], ["not_empty", "Is not empty"],
    ["gt", "Greater than"], ["gte", "Greater than or equal to"], ["lt", "Less than"], ["lte", "Less than or equal to"],
    ["between", "Is between"], ["regex", "Matches regex"]];
  const OP_SHORT = { contains: "", not_contains: "not ", equals: "= ", not_equals: "≠ ", starts: "starts ", ends: "ends ", empty: "(empty)",
    not_empty: "(not empty)", gt: "> ", gte: "≥ ", lt: "< ", lte: "≤ ", between: "", regex: "regex " };
  const NO_VALUE = new Set(["none", "empty", "not_empty"]);

  function colFilterList() {
    const out = [];
    Object.entries(S.colFilters).forEach(([field, f]) => {
      if (f.cond) out.push({ field, ...f.cond });
      if (f.vals) out.push({ field, op: f.vals.mode, values: f.vals.list });
    });
    return out;
  }
  function filtersQS(skipField) {
    const l = colFilterList().filter((f) => f.field !== skipField);
    return l.length ? `&filters=${encodeURIComponent(JSON.stringify(l))}` : "";
  }
  function colFilterSummary(f) {
    if (!f) return "";
    const parts = [];
    if (f.cond) parts.push(f.cond.op === "between" ? `${f.cond.value} – ${f.cond.value2}` : `${OP_SHORT[f.cond.op] || ""}${NO_VALUE.has(f.cond.op) ? "" : f.cond.value}`);
    if (f.vals) parts.push(f.vals.mode === "in" ? `${fmtNum(f.vals.list.length)} value${f.vals.list.length === 1 ? "" : "s"}` : `all but ${fmtNum(f.vals.list.length)}`);
    return parts.join(" + ");
  }
  function isPlainContains(f) { return f && f.cond && f.cond.op === "contains" && !f.vals; }
  function updateColFilterBar() {
    const n = Object.keys(S.colFilters).length, b = $("#colfilters-clear");
    b.classList.toggle("hidden", !n);
    b.textContent = `Clear column filters (${n})`;
  }
  function applyColFilters() {
    updateColFilterBar();
    if (S.table) {
      S.table.getColumns().forEach((c) => {
        const el = c.getElement().querySelector(".hf"); if (!el) return;
        const f = S.colFilters[c.getField()], inp = el.querySelector("input");
        if (document.activeElement !== inp) inp.value = isPlainContains(f) ? f.cond.value : colFilterSummary(f);
        el.classList.toggle("on", !!f);
      });
      S.table.setData();
    }
  }
  $("#colfilters-clear").onclick = () => { S.colFilters = {}; closeColFilter(); applyColFilters(); };

  function headerFilterEl(cell) {
    const field = cell.getField(), f = S.colFilters[field];
    const wrap = document.createElement("div");
    wrap.className = `hf${f ? " on" : ""}`;
    wrap.innerHTML = `<input type="text" spellcheck="false" placeholder="Filter" aria-label="Filter ${esc(cell.getColumn().getDefinition().title)}"><button type="button" title="Filter options: conditions and values" aria-label="Filter options">${ic("filter")}</button>`;
    const inp = $("input", wrap), btn = $("button", wrap);
    inp.value = isPlainContains(f) ? f.cond.value : colFilterSummary(f);
    let t;
    inp.onfocus = () => { if (S.colFilters[field] && !isPlainContains(S.colFilters[field])) inp.select(); };
    inp.oninput = () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const v = inp.value.trim();
        if (v) S.colFilters[field] = { cond: { op: "contains", value: v }, vals: null }; else delete S.colFilters[field];
        applyColFilters();
      }, 350);
    };
    inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Escape" && inp.value) { inp.value = ""; inp.oninput(); } };
    btn.onclick = (e) => { e.stopPropagation(); openColFilter(field, cell.getColumn().getDefinition().title, btn); };
    return wrap;
  }

  let cfPop = null;
  function closeColFilter() { if (cfPop) { cfPop.remove(); cfPop = null; } }
  document.addEventListener("mousedown", (e) => { if (cfPop && !cfPop.contains(e.target) && !e.target.closest(".hf button")) closeColFilter(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && cfPop) closeColFilter(); });

  async function openColFilter(field, title, anchor) {
    if (cfPop && cfPop.dataset.field === field) { closeColFilter(); return; }
    closeColFilter();
    const cur = S.colFilters[field] || {};
    const pop = cfPop = document.createElement("div");
    pop.className = "cf-pop"; pop.dataset.field = field;
    pop.innerHTML = `<div class="cf-title">Filter <b>${esc(title)}</b></div>
      <div class="cf-sec">By condition</div>
      <select class="cf-op">${COND_OPS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <div class="cf-vals"><input class="cf-v1" type="text" spellcheck="false" placeholder="Value"><span class="cf-and">and</span><input class="cf-v2" type="text" spellcheck="false" placeholder="Value"></div>
      <div class="cf-sec">By values</div>
      <input class="cf-search" type="search" placeholder="Search values">
      <div class="cf-links"><button type="button" class="link cf-all">Select all</button><button type="button" class="link cf-none">Clear</button><span class="cf-picked muted"></span></div>
      <div class="cf-list"><div class="loading-inline"><span class="spinner"></span>Loading values</div></div>
      <div class="cf-note muted hidden"></div>
      <div class="cf-acts"><button type="button" class="cf-reset">Remove filter</button><span class="spacer"></span><button type="button" class="cf-cancel">Cancel</button><button type="button" class="primary cf-apply">Apply</button></div>`;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    pop.style.top = `${Math.min(r.bottom + 4, window.innerHeight - pop.offsetHeight - 8)}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, window.innerWidth - pop.offsetWidth - 8))}px`;

    const op = $(".cf-op", pop), v1 = $(".cf-v1", pop), v2 = $(".cf-v2", pop);
    op.value = cur.cond ? cur.cond.op : "none"; v1.value = cur.cond ? cur.cond.value || "" : ""; v2.value = cur.cond ? cur.cond.value2 || "" : "";
    const syncOp = () => { const o = op.value; $(".cf-vals", pop).classList.toggle("hidden", NO_VALUE.has(o)); pop.classList.toggle("between", o === "between"); v1.placeholder = o === "regex" ? "Regular expression" : "Value"; };
    op.onchange = () => { syncOp(); if (!NO_VALUE.has(op.value)) v1.focus(); };
    syncOp();

    let values = [], truncated = false;
    const checked = new Set();
    const list = $(".cf-list", pop), search = $(".cf-search", pop);
    const label = (v) => (v === "" ? "(Blanks)" : v);
    const visible = () => { const s = search.value.trim().toLowerCase(); return s ? values.filter((x) => label(x.value).toLowerCase().includes(s)) : values; };
    const renderList = () => {
      const vis = visible();
      list.innerHTML = vis.length
        ? vis.slice(0, 2000).map((x, i) => `<label class="cf-item"><input type="checkbox" data-i="${values.indexOf(x)}" ${checked.has(x.value) ? "checked" : ""}><span class="v ${x.value === "" ? "muted" : ""}" title="${esc(label(x.value))}">${esc(label(x.value))}</span><span class="n">${fmtNum(x.count)}</span></label>`).join("")
        : `<div class="muted pad">${values.length ? "No values match" : "No values"}</div>`;
      $(".cf-picked", pop).textContent = values.length ? `${fmtNum(checked.size)} of ${fmtNum(values.length)}${truncated ? "+" : ""} selected` : "";
    };
    list.onchange = (e) => { const x = values[Number(e.target.dataset.i)]; if (!x) return; e.target.checked ? checked.add(x.value) : checked.delete(x.value); renderList(); };
    search.oninput = renderList;
    $(".cf-all", pop).onclick = () => { visible().forEach((x) => checked.add(x.value)); renderList(); };
    $(".cf-none", pop).onclick = () => { visible().forEach((x) => checked.delete(x.value)); renderList(); };
    $(".cf-cancel", pop).onclick = closeColFilter;
    $(".cf-reset", pop).onclick = () => { delete S.colFilters[field]; closeColFilter(); applyColFilters(); };
    const apply = () => {
      const o = op.value, next = { cond: null, vals: null };
      if (o !== "none") {
        if (!NO_VALUE.has(o) && !v1.value.trim()) { v1.focus(); toast("Enter a value for the condition", "warn"); return; }
        if (o === "between" && !v2.value.trim()) { v2.focus(); toast("Enter both values for Is between", "warn"); return; }
        if (o === "regex") { try { new RegExp(v1.value.trim()); } catch (err) { v1.focus(); toast(`Invalid regular expression: ${err.message}`, "warn"); return; } }
        next.cond = { op: o, value: v1.value.trim() }; if (o === "between") next.cond.value2 = v2.value.trim();
      }
      if (values.length) {
        const on = values.filter((x) => checked.has(x.value)).map((x) => x.value), off = values.filter((x) => !checked.has(x.value)).map((x) => x.value);
        if (off.length) next.vals = on.length <= off.length ? { mode: "in", list: on } : { mode: "not_in", list: off };
      }
      if (next.cond || next.vals) S.colFilters[field] = next; else delete S.colFilters[field];
      closeColFilter(); applyColFilters();
    };
    $(".cf-apply", pop).onclick = apply;
    pop.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.type !== "checkbox") { e.preventDefault(); apply(); } });

    try {
      const d = await api(`/api/urls/values?key=${encodeURIComponent(S.filter)}&field=${encodeURIComponent(field)}&q=${encodeURIComponent(S.q)}${filtersQS(field)}`);
      if (cfPop !== pop) return;
      values = d.values.sort((a, b) => String(a.value).localeCompare(String(b.value), undefined, { numeric: true, sensitivity: "base" }));
      truncated = d.truncated;
      values.forEach((x) => { if (!cur.vals || (cur.vals.mode === "in" ? cur.vals.list.includes(x.value) : !cur.vals.list.includes(x.value))) checked.add(x.value); });
      if (truncated) { const n = $(".cf-note", pop); n.textContent = `Showing the ${fmtNum(values.length)} most common values. Use a condition to reach the rest.`; n.classList.remove("hidden"); }
      renderList();
    } catch (e) { if (cfPop === pop) list.innerHTML = `<div class="muted pad">Could not load values: ${esc(e.message)}</div>`; }
  }

  $("#sel-clear").onclick = (e) => { e.preventDefault(); S.table && S.table.deselectRow(); };
  $("#sel-copy").onclick = async (e) => {
    e.preventDefault();
    const urls = (S.table ? S.table.getSelectedData() : []).map((r) => r.url).join("\n");
    try { await navigator.clipboard.writeText(urls); toast(`Copied ${fmtNum(urls.split("\n").length)} URLs`); } catch (_) { toast("Clipboard not available", "warn"); }
  };
  $("#sel-export").onclick = (e) => {
    e.preventDefault();
    const rows = S.table ? S.table.getSelectedData() : [];
    if (!rows.length) return;
    const cols = S.columns[S.tab] || S.columns.internal;
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = "﻿" + [cols.map((c) => q(c.title)).join(","), ...rows.map((r) => cols.map((c) => q(r[c.field])).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); a.download = `${S.filter.replace(":", "_")}_selected.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast(`Exported ${fmtNum(rows.length)} rows`);
  };

  async function refreshTableSoft() {
    if (!S.table || S.tableLoading) return;
    try { await S.table.setData(); } catch (_) {}
  }

  // ------------------------------------------------------------------ detail pane
  async function showDetail(id) {
    S.selectedId = id;
    $("#bottom-body").innerHTML = skel.kv(8);
    try { S.detail = await api(`/api/url/${id}`); } catch (e) { $("#bottom-body").innerHTML = empty("!", "Could not load URL details", e.message); return; }
    if (S.selectedId === id) renderBottom();
  }

  const linkTable = (rows, withSource) => {
    if (!rows.length) return `<div class="pad muted">None</div>`;
    const head = `<tr><th>Type</th>${withSource ? "<th>From</th>" : "<th>To</th>"}<th>Anchor</th><th>Alt</th><th>Status</th><th>Follow</th><th>Target</th><th>Rel</th><th>Position</th><th>Path</th></tr>`;
    const body = rows.map((l) => `<tr><td>${esc(l.kind)}</td><td title="${esc(withSource ? l.source : l.destination)}">${esc(withSource ? l.source : l.destination)}</td><td>${esc(l.anchor)}</td><td>${l.alt == null ? "<i class=muted>(no attr)</i>" : esc(l.alt)}</td><td class="${statusClass(l.status_code)}">${l.status_code ?? ""} ${esc(l.status || "")}</td><td>${l.follow ? "True" : "False"}</td><td>${esc(l.target)}</td><td>${esc(l.rel)}</td><td>${esc(l.position)}</td><td class="muted">${esc(l.path)}</td></tr>`).join("");
    return `<table>${head}${body}</table>`;
  };

  async function renderBottom() {
    const d = S.detail, body = $("#bottom-body");
    if (!d) { body.innerHTML = empty("corner-left-up", "Select a URL", "Click a row in the table above. Double-click opens the page in your browser."); return; }
    const pane = S.bottomPane;
    if (pane === "details") {
      const skip = new Set(["inlinks", "outlinks", "images", "resources", "issues", "issue_names", "uncrawlable", "headers", "cookies", "headings", "hreflang", "custom", "sitemaps", "exact_duplicates", "near_duplicates", "spelling", "structured_data", "lighthouse"]);
      const rows = Object.entries(d).filter(([k, v]) => !skip.has(k) && v !== null && v !== "" && v !== 0 && k !== "id");
      let html = `<table>${rows.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`).join("")}`;
      if (d.headings && d.headings.length) html += `<tr><td class="k">headings</td><td>${d.headings.map((h) => `<div>${"&nbsp;".repeat((h[0] - 1) * 3)}H${h[0]}: ${esc(h[1])}</div>`).join("")}</td></tr>`;
      if (d.hreflang && d.hreflang.length) html += `<tr><td class="k">hreflang</td><td>${d.hreflang.map((h) => `<div>${esc(h.lang)} → ${esc(h.url)} <span class="muted">(${esc(h.src)})</span></div>`).join("")}</td></tr>`;
      if (d.custom) html += `<tr><td class="k">custom</td><td><pre>${esc(JSON.stringify(d.custom, null, 1))}</pre></td></tr>`;
      if (d.sitemaps) html += `<tr><td class="k">sitemaps</td><td>${d.sitemaps.map(esc).join("<br>")}</td></tr>`;
      body.innerHTML = html + "</table>";
    } else if (pane === "inlinks") body.innerHTML = linkTable(d.inlinks, true);
    else if (pane === "outlinks") body.innerHTML = linkTable(d.outlinks.filter((l) => l.kind === "hyperlink" || l.kind === "redirect"), false) + (d.uncrawlable.length ? `<div class="pad"><b>Uncrawlable links</b><table>${d.uncrawlable.map((u) => `<tr><td>${esc(u.href)}</td><td>${esc(u.anchor)}</td><td class="muted">${esc(u.reason)}</td></tr>`).join("")}</table></div>` : "");
    else if (pane === "images") {
      body.innerHTML = d.images.length ? `<table><tr><th>Image</th><th>Alt</th><th>Size</th><th>Status</th><th>Size attrs</th><th>Background</th><th>Path</th></tr>${d.images.map((i) => `<tr><td>${esc(i.destination)}</td><td>${i.alt == null ? "<i class=muted>(missing attribute)</i>" : esc(i.alt) || "<i class=muted>(empty)</i>"}</td><td>${fmtNum(i.size ?? "")}</td><td class="${statusClass(i.status_code)}">${i.status_code ?? ""}</td><td>${i.has_size_attrs ? "Yes" : "No"}</td><td>${i.is_bg ? "Yes" : ""}</td><td class="muted">${esc(i.path)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No images on this page</div>`;
    } else if (pane === "resources") body.innerHTML = d.resources.length ? `<table><tr><th>Type</th><th>Resource</th><th>Status</th><th>Size</th></tr>${d.resources.map((r) => `<tr><td>${esc(r.kind)}</td><td>${esc(r.destination)}</td><td class="${statusClass(r.status_code)}">${r.status_code ?? ""}</td><td>${fmtNum(r.size ?? "")}</td></tr>`).join("")}</table>` : `<div class="pad muted">No CSS/JS/iframe/media resources</div>`;
    else if (pane === "serp") {
      const u = d.url.replace(/^https?:\/\//, "").replace(/\/$/, "").split("/").join(" › ");
      body.innerHTML = `<div class="serp"><div class="u">${esc(u)}</div><div class="t">${esc(d.title || "(no title)")}</div><div class="d">${esc(d.meta_desc || "(no meta description)")}</div><div class="muted" style="margin-top:10px">Title: ${d.title_len} chars / ${d.title_px}px &nbsp; Description: ${d.md_len} chars / ${d.md_px}px</div></div>`;
    } else if (pane === "headers") body.innerHTML = d.headers ? `<table>${Object.entries(d.headers).map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No headers stored</div>`;
    else if (pane === "cookies") body.innerHTML = d.cookies && d.cookies.length ? `<table>${d.cookies.map((c) => `<tr><td>${esc(c)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No cookies set</div>`;
    else if (pane === "source") {
      body.innerHTML = skel.rows(8);
      const kind = d.has_rendered_html && S.sourceKind === "rendered" ? "rendered" : "html";
      const r = await fetch(`/api/url/${d.id}/source?kind=${kind}`); const txt = await r.text();
      const toggle = d.has_rendered_html ? `<div class="pad"><label><input type="radio" name="sk" value="html" ${kind === "html" ? "checked" : ""}> Original HTML</label> &nbsp; <label><input type="radio" name="sk" value="rendered" ${kind === "rendered" ? "checked" : ""}> Rendered HTML</label></div>` : "";
      body.innerHTML = toggle + (txt ? `<pre>${esc(txt.slice(0, 400000))}</pre>` : `<div class="pad muted">No HTML stored for this URL</div>`);
      $$("input[name=sk]", body).forEach((r) => r.onchange = () => { S.sourceKind = r.value; renderBottom(); });
    } else if (pane === "rendered") {
      body.innerHTML = d.has_screenshot ? `<div class="pad"><img src="/api/url/${d.id}/screenshot?t=${Date.now()}" style="max-width:100%;border:1px solid var(--border)"></div>` :
        `<div class="pad muted">${d.rendered ? "No screenshot stored." : "Not rendered. Set Config → Rendering to JavaScript and re-crawl to see the rendered page."}</div>`;
    } else if (pane === "console") {
      const errs = Array.isArray(d.js_errors_text) ? d.js_errors_text : [];
      const blocked = Array.isArray(d.js_blocked_text) ? d.js_blocked_text : [];
      body.innerHTML = !d.rendered ? `<div class="pad muted">${d.render_error ? "Render failed: " + esc(d.render_error) : "Not rendered (text-only crawl)."}</div>` :
        `<div class="pad"><b>Console errors (${errs.length})</b>${errs.length ? `<pre>${errs.map(esc).join("\n")}</pre>` : "<div class=muted>None</div>"}<b>Blocked resources (${blocked.length})</b>${blocked.length ? `<pre>${blocked.map(esc).join("\n")}</pre>` : "<div class=muted>None</div>"}<div class="muted">Render time ${d.render_time}s · HTML word count ${d.orig_word_count ?? ""} → rendered ${d.word_count ?? ""}</div></div>`;
    } else if (pane === "pagespeed") {
      const lh = d.lighthouse;
      if (!lh) { body.innerHTML = `<div class="pad muted">No Lighthouse data for this page yet. <button type="button" id="lh-run-one">${ic("gauge")}Run Lighthouse for this URL</button> <span class="muted">(~25 s locally)</span></div>`; const b1 = $("#lh-run-one", body); if (b1) b1.onclick = () => runLighthouse("ids", [d.id]); }
      else {
        const cls = (v) => v == null ? "" : v >= 90 ? "lh-good" : v >= 50 ? "lh-avg" : "lh-poor";
        const ms = (v) => v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
        const m = lh.metrics, f = lh.field || {};
        const mcls = (k, v) => v == null ? "" : ({ lcp: v <= 2500 ? "lh-good" : v <= 4000 ? "lh-avg" : "lh-poor", cls: v <= 0.1 ? "lh-good" : v <= 0.25 ? "lh-avg" : "lh-poor", tbt: v <= 200 ? "lh-good" : v <= 600 ? "lh-avg" : "lh-poor", inp: v <= 200 ? "lh-good" : v <= 500 ? "lh-avg" : "lh-poor", fcp: v <= 1800 ? "lh-good" : v <= 3000 ? "lh-avg" : "lh-poor" }[k] || "");
        const scores = [["performance", "Performance"], ["accessibility", "Accessibility"], ["best-practices", "Best Practices"], ["seo", "SEO"]].map(([k, l]) => `<div class="lh-score"><div class="v ${cls(lh.scores[k])}">${lh.scores[k] ?? "—"}</div><div class="l">${l}</div></div>`).join("");
        const metrics = `<table class="lh-metrics"><tr><th></th><th>Lab</th>${Object.keys(f).length ? "<th>Field (CrUX p75)</th>" : ""}</tr>
          <tr><td>LCP</td><td class="${mcls("lcp", m.lcp)}">${ms(m.lcp)}</td>${Object.keys(f).length ? `<td class="${mcls("lcp", f.lcp)}">${ms(f.lcp)}</td>` : ""}</tr>
          <tr><td>CLS</td><td class="${mcls("cls", m.cls)}">${m.cls == null ? "—" : m.cls.toFixed(3)}</td>${Object.keys(f).length ? `<td class="${mcls("cls", f.cls)}">${f.cls == null ? "—" : f.cls.toFixed(3)}</td>` : ""}</tr>
          <tr><td>TBT / INP</td><td class="${mcls("tbt", m.tbt)}">${ms(m.tbt)}</td>${Object.keys(f).length ? `<td class="${mcls("inp", f.inp)}">${ms(f.inp)} INP</td>` : ""}</tr>
          <tr><td>FCP</td><td class="${mcls("fcp", m.fcp)}">${ms(m.fcp)}</td>${Object.keys(f).length ? `<td class="${mcls("fcp", f.fcp)}">${ms(f.fcp)}</td>` : ""}</tr>
          <tr><td>Speed Index</td><td>${ms(m.si)}</td>${Object.keys(f).length ? "<td></td>" : ""}</tr><tr><td>Time to Interactive</td><td>${ms(m.tti)}</td>${Object.keys(f).length ? "<td></td>" : ""}</tr></table>`;
        const audit = (a) => `<details class="lh-audit"><summary><span class="badge ${a.score < 0.5 ? "issue" : "warning"}"></span><b>${esc(a.title)}</b>${a.display ? ` <span class="muted">${esc(a.display)}</span>` : ""}<span class="sv">${a.savings_ms ? `save ~${ms(a.savings_ms)}` : ""}${a.savings_bytes ? ` ${a.savings_ms ? "·" : ""} ${fmtNum(Math.round(a.savings_bytes / 1024))} KB` : ""}${a.items ? ` · ${fmtNum(a.items)} item${a.items > 1 ? "s" : ""}` : ""}</span></summary>
          ${a.description ? `<p>${esc(a.description.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1"))}</p>` : ""}${a.sample.length ? `<table>${a.sample.map((s) => `<tr><td>${esc(s.url || s.selector || s.label || "")}</td><td class="muted">${esc(s.snippet || "")}</td><td>${s.wasted_ms ? ms(s.wasted_ms) : s.wasted_bytes ? fmtNum(Math.round(s.wasted_bytes / 1024)) + " KB" : s.total_bytes ? fmtNum(Math.round(s.total_bytes / 1024)) + " KB" : s.duration ? ms(s.duration) : ""}</td></tr>`).join("")}</table>` : ""}</details>`;
        const perf = lh.failed.filter((a) => a.group === "performance"), a11y = lh.failed.filter((a) => a.group === "accessibility");
        body.innerHTML = `<div class="pad"><div class="lh-scores">${scores}<div class="muted" style="align-self:center;font-size:var(--fs-xs)">${esc(lh.source === "psi" ? "PageSpeed Insights" : "Lighthouse " + (lh.version || ""))} · ${esc(lh.device)} · ${esc((lh.fetched_at || "").replace("T", " ").slice(0, 16))}<br><button type="button" id="lh-run-one">${ic("refresh-cw")}Re-run</button></div></div>${metrics}
          <b style="display:block;margin-top:12px">Opportunities & diagnostics (${perf.length})</b>${perf.length ? perf.map(audit).join("") : "<div class=muted>All performance audits pass.</div>"}
          <b style="display:block;margin-top:12px">Accessibility (${a11y.length} failing axe checks)</b>${a11y.length ? a11y.map(audit).join("") : "<div class=muted>All accessibility audits pass.</div>"}</div>`;
        $("#lh-run-one", body).onclick = () => runLighthouse("ids", [d.id]);
      }
    } else if (pane === "structured") {
      const sd = d.structured_data || [];
      body.innerHTML = sd.length ? `<div class="pad">${sd.map((it, i) => `<details class="sd-item" ${it.errors.length || i === 0 ? "open" : ""}><summary><span class="badge ${it.errors.length ? "issue" : it.warnings.length ? "warning" : "opportunity"}"></span><b>${esc(it.type)}</b> <span class="muted">${esc(it.syntax)}</span>${it.errors.length ? ` <span class="delta-up">${it.errors.length} error${it.errors.length > 1 ? "s" : ""}</span>` : ""}${it.warnings.length ? ` <span class="muted">${it.warnings.length} warning${it.warnings.length > 1 ? "s" : ""}</span>` : ""}</summary>
          ${it.errors.length ? `<ul class="sd-msgs err">${it.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}${it.warnings.length ? `<ul class="sd-msgs warn">${it.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
          <pre>${esc(typeof it.data === "string" ? it.data : JSON.stringify(it.data, null, 2))}</pre></details>`).join("")}</div>`
        : `<div class="pad muted">${S.config.extract_structured_data === false ? "Structured data extraction is off (Config → Crawl)." : "No JSON-LD, Microdata or RDFa on this page."}</div>`;
    } else if (pane === "duplicates") {
      const ex = d.exact_duplicates || [], nr = d.near_duplicates || [];
      body.innerHTML = (ex.length || nr.length) ? `<div class="pad"><b>Exact duplicates (${fmtNum(ex.length)})</b>${ex.length ? `<table>${ex.map((u) => `<tr><td><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></td><td class="muted">identical HTML</td></tr>`).join("")}</table>` : "<div class=muted>None</div>"}
        <b style="display:block;margin-top:10px">Near duplicates (${fmtNum(nr.length)})</b><span class="muted" style="font-size:var(--fs-xs)"> text similarity ≥ ${S.config.near_duplicate_threshold || 90}% (Config → Preferences)</span>${nr.length ? `<table><tr><th>URL</th><th>Similarity</th></tr>${nr.map((n) => `<tr><td><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.url)}</a></td><td>${n.similarity}%</td></tr>`).join("")}</table>` : "<div class=muted>None</div>"}</div>`
        : `<div class="pad muted">No exact or near duplicates for this page${d.word_count && d.word_count < 30 ? " (pages under 30 words are not compared)" : ""}.</div>`;
    } else if (pane === "spelling") {
      const f = d.spelling || [], sp = f.filter((x) => x.kind === "spelling"), gr = f.filter((x) => x.kind === "grammar");
      const row = (x, ignorable) => `<tr><td><b>${esc(x.text)}</b></td><td>${esc(x.suggestion || "")}</td><td>${esc(x.message || "")}</td><td class="muted">${esc(x.context || "")}</td><td>${ignorable ? `<button type="button" data-ignore="${esc(x.text)}" title="Never flag this word again (adds it to the dictionary and re-checks)">Add to dictionary</button>` : ""}</td></tr>`;
      if (!S.config.spell_check && !S.config.grammar_check && !f.length) body.innerHTML = `<div class="pad muted">Spelling & grammar checks are off. Turn them on in <a href="#" id="sp-cfg">Config → Spelling & grammar</a>, then press Analyse (or "Run check now").</div>`;
      else body.innerHTML = `<div class="pad"><b>Spelling (${fmtNum(sp.length)})</b>${sp.length ? `<table><tr><th>Word</th><th>Suggestion</th><th>Note</th><th>Context</th><th></th></tr>${sp.map((x) => row(x, true)).join("")}</table>` : "<div class=muted>None</div>"}
        <b style="display:block;margin-top:10px">Grammar (${fmtNum(gr.length)})</b>${gr.length ? `<table><tr><th>Text</th><th>Suggestion</th><th>Rule</th><th>Context</th><th></th></tr>${gr.map((x) => row(x, false)).join("")}</table>` : "<div class=muted>None</div>"}</div>`;
      const cfgLink = $("#sp-cfg", body); if (cfgLink) cfgLink.onclick = (e) => { e.preventDefault(); $("#btn-config").onclick(null, "Spelling & grammar"); };
      $$("[data-ignore]", body).forEach((b) => b.onclick = () => busy(b, async () => {
        try { const r = await post("/api/spelling/ignore", { word: b.dataset.ignore }); toast(`'${b.dataset.ignore}' added to the dictionary (${fmtNum(r.ignored)} words) — pages re-checked`); S.config.spell_ignore = [...(S.config.spell_ignore || []), b.dataset.ignore]; await showDetail(d.id); await loadTabs(); refreshTableSoft(); }
        catch (e) { toast(e.message, "error"); }
      }));
    } else if (pane === "issues") body.innerHTML = d.issue_names.length ? `<table>${d.issue_names.map((n) => `<tr><td>${esc(n)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No issues for this URL</div>`;
  }

  $$("#bottom-tabs button").forEach((b) => b.onclick = () => { $$("#bottom-tabs button").forEach((x) => x.classList.toggle("active", x === b)); S.bottomPane = b.dataset.pane; renderBottom(); });

  // ------------------------------------------------------------------ sidebar
  $$("#side-tabs button").forEach((b) => b.onclick = () => { $$("#side-tabs button").forEach((x) => x.classList.toggle("active", x === b)); S.sidePane = b.dataset.side; renderSide(); });

  async function renderSide() {
    const el = $("#side-body");
    if (S.sidePane === "overview") {
      el.innerHTML = S.tabs.map((t) => `<div class="ov-tab" data-tab="${t.key}">${esc(t.name)}<span class="n">${fmtNum((t.filters[0] || {}).count || 0)}</span></div>` +
        t.filters.filter((f) => !f.issue_only).map((f) => `<div class="ov-filter ${f.key === S.filter ? "active" : ""} ${f.count ? "" : "zero"}" data-filter="${f.key}" data-tab="${t.key}">${f.kind ? `<span class="badge ${f.kind}" title="${f.kind}"></span>` : ""}${esc(f.name)}<span class="n">${fmtNum(f.count)}</span></div>`).join("")).join("");
      $$(".ov-filter", el).forEach((x) => x.onclick = () => selectTab(x.dataset.tab, x.dataset.filter));
      $$(".ov-tab", el).forEach((x) => x.onclick = () => selectTab(x.dataset.tab));
      const act = $(".ov-filter.active", el); if (act) act.scrollIntoView({ block: "nearest" });
    } else if (S.sidePane === "issues") {
      if (!el.dataset.loaded) el.innerHTML = skel.list(12);
      try {
        const d = await api("/api/issues");
        el.dataset.loaded = "1";
        const legend = `<div class="legend"><span><i class="badge issue"></i>Issue</span><span><i class="badge warning"></i>Warning</span><span><i class="badge opportunity"></i>Opportunity</span><span style="margin-left:auto">sorted by priority</span></div>`;
        el.innerHTML = legend + (d.issues.length ? d.issues.map((i) => `<div class="issue-row" data-filter="${i.key}" data-tab="${i.key.split(":")[0]}"><div class="t"><span class="badge ${i.kind}" title="${i.kind}"></span><b>${esc(i.tab)}: ${esc(i.name)}</b><span>${fmtNum(i.count)}</span></div><div class="d"><span class="prio">${esc(i.priority)} priority</span> · ${esc(i.desc)}</div></div>`).join("") : empty("check", "No issues found", S.status.state === "idle" ? "Start a crawl to see issues here." : "Nothing flagged so far."));
        $$(".issue-row", el).forEach((x) => x.onclick = () => selectTab(x.dataset.tab, x.dataset.filter));
        syncSideHighlight();
      } catch (e) { el.innerHTML = empty("spider", "No crawl open", "Start a crawl or open a saved one."); }
    } else {
      if (!el.dataset.loaded) el.innerHTML = skel.list(12);
      try {
        const [o, tree] = await Promise.all([api("/api/overview"), api("/api/structure")]);
        el.dataset.loaded = "1";
        const bar = (rows) => `<table>${rows.map((r) => `<tr><td>${esc(r[0] ?? "(none)")}</td><td style="text-align:right">${fmtNum(r[1])}</td></tr>`).join("")}</table>`;
        const total = tree.pages || 1;
        const node = (n, depth) => {
          const inner = `<span class="nm" title="${esc(n.path)}">${esc(n.name)}</span><span class="bar"><i style="width:${Math.max(2, Math.round(100 * n.pages / total))}%"></i></span><span class="n">${fmtNum(n.pages)}</span>`;
          if (!n.children.length) return `<div class="leaf" data-path="${esc(n.path)}">${inner}</div>`;
          return `<details ${depth < 1 ? "open" : ""}><summary data-path="${esc(n.path)}">${inner}</summary><div class="kids">${n.children.map((c) => node(c, depth + 1)).join("")}${n.more ? `<div class="leaf muted">… ${fmtNum(n.more)} more folders</div>` : ""}</div></details>`;
        };
        el.innerHTML = `<div class="ov-tab">Summary</div><table><tr><td>Total URLs</td><td>${fmtNum(o.total)}</td></tr><tr><td>Internal</td><td>${fmtNum(o.internal)}</td></tr><tr><td>External</td><td>${fmtNum(o.external)}</td></tr><tr><td>Indexable</td><td>${fmtNum(o.indexable)}</td></tr><tr><td>Non-indexable</td><td>${fmtNum(o.non_indexable)}</td></tr></table>
          <div class="ov-tab">Site structure <span class="n muted" style="font-weight:400">HTML pages per folder · click to filter</span></div><div class="tree">${node(tree, 0)}</div>
          <div class="ov-tab">Crawl depth (HTML)</div>${bar(o.depth)}<div class="ov-tab">Status codes</div>${bar(o.status)}<div class="ov-tab">Response times</div>${bar(o.response_times)}`;
        $$(".tree [data-path]", el).forEach((x) => x.addEventListener("click", (ev) => {
          if (ev.target.closest("summary") && ev.offsetX < 16) return;   // the disclosure arrow toggles only
          ev.preventDefault();
          const p = x.dataset.path;
          $("#search").value = p === "/" ? "" : p; S.q = $("#search").value;
          if (S.tab !== "internal") selectTab("internal", "internal:html"); else { S.filter = "internal:html"; renderFilterSelect(); S.table && S.table.setData(); }
          toast(`Filtered to ${p}`);
        }));
      } catch (e) { el.innerHTML = empty("spider", "No crawl open", "Start a crawl or open a saved one."); }
    }
  }

  // ------------------------------------------------------------------ crawl control
  function urlProblem(raw) {
    const v = raw.trim();
    if (!v) return "Enter a website address";
    if (/\s/.test(v)) return "Addresses cannot contain spaces";
    const host = v.replace(/^https?:\/\//i, "").split(/[/?#]/)[0];
    if (!host.includes(".") && host !== "localhost") return "Needs a domain, e.g. www.example.com";
    return "";
  }
  function refreshStartState() {
    const mode = $("#mode").value, inp = $("#start-url"), btn = $("#btn-start");
    const running = ["running", "paused", "stopping"].includes(S.status.state);
    let problem = mode === "list" ? (S.listUrls.length ? "" : "Paste URLs first (URLs… button)") : urlProblem(inp.value);
    inp.classList.toggle("invalid", !!problem && inp.value.trim().length > 0 && mode !== "list");
    setAttr(btn, "disabled", running || !!problem);
    setAttr(btn, "title", running ? "A crawl is already running" : problem || (mode === "sitemap" ? "Fetch every URL listed in this sitemap" : mode === "list" ? `Crawl the ${S.listUrls.length} pasted URLs` : "Crawl this site"));
  }
  $("#start-url").addEventListener("input", refreshStartState);
  async function start() {
    const mode = $("#mode").value;
    refreshStartState();
    if ($("#btn-start").disabled) { toast($("#btn-start").title, "warn"); $("#start-url").focus(); return; }
    const body = { start_url: $("#start-url").value.trim(), mode, list_urls: mode === "list" ? S.listUrls : [] };
    await busy($("#btn-start"), async () => {
      try { await post("/api/crawl/start", body); } catch (e) { toast(e.message, "error", 8000); $("#start-url").classList.add("invalid"); $("#start-url").focus(); return; }
      S.selectedId = null; S.detail = null; $("#bottom-body").innerHTML = empty("corner-left-up", "Select a URL", "Click a row in the table to see its details, inlinks, headers and source.");
      $$("#side-body")[0].dataset.loaded = "";
      await loadTabs(); selectTab("internal");
      toast(mode === "sitemap" ? "Fetching URLs from the sitemap…" : "Crawl started");
    });
  }
  $("#btn-start").onclick = start;
  $("#start-url").onkeydown = (e) => { if (e.key === "Enter") start(); };
  $("#btn-pause").onclick = async () => { await post(S.status.state === "paused" ? "/api/crawl/resume" : "/api/crawl/pause"); poll(); };
  $("#btn-stop").onclick = async (e) => {
    const b = e.target;
    if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Confirm stop"; toast("Stopping keeps everything crawled so far. Click Stop again to confirm.", "warn"); setTimeout(() => { b.dataset.armed = ""; b.textContent = "Stop"; }, 4000); return; }
    b.dataset.armed = ""; b.textContent = "Stop"; await post("/api/crawl/stop"); poll();
  };
  $("#mode").onchange = (e) => {
    const m = e.target.value, list = m === "list";
    $("#btn-list").classList.toggle("hidden", !list); $("#start-url").classList.toggle("hidden", list);
    $("#start-url").placeholder = m === "sitemap" ? "Enter XML sitemap or sitemap index URL, e.g. https://www.example.com/sitemap.xml" : "Enter URL to spider, e.g. https://www.example.com";
    $("#mode").title = { spider: "Spider: start at a URL and follow every internal link", sitemap: "Sitemap: fetch exactly the URLs listed in an XML sitemap, no link following", list: "List: fetch exactly the URLs you paste" }[m];
    if (list) openModal("modal-list");
    refreshStartState();
  };
  $("#mode").title = "Spider: start at a URL and follow every internal link";
  $("#btn-list").onclick = () => openModal("modal-list");
  $("#list-urls").oninput = (e) => {
    const lines = e.target.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const bad = lines.filter((l) => urlProblem(l));
    S.listUrls = lines.filter((l) => !urlProblem(l));
    $("#list-info").innerHTML = `${fmtNum(S.listUrls.length)} URL${S.listUrls.length === 1 ? "" : "s"} ready${bad.length ? `<div class="list-bad">${bad.length} line${bad.length > 1 ? "s" : ""} ignored (not a URL): ${esc(bad.slice(0, 3).join(" · "))}${bad.length > 3 ? " …" : ""}</div>` : ""}`;
    $("#btn-list").textContent = S.listUrls.length ? `${fmtNum(S.listUrls.length)} URLs…` : "URLs…";
    refreshStartState();
  };
  $("#btn-analysis").onclick = () => busy($("#btn-analysis"), async () => {
    if (!S.status || S.status.state === "idle") { toast("Open or start a crawl first", "warn"); return; }
    if (["running", "paused", "stopping"].includes(S.status.state)) { toast("Analysis already runs every 15s while crawling and once more at the end", "warn"); return; }
    try { await post("/api/analysis/run"); $("#side-body").dataset.loaded = ""; await loadTabs(); refreshTableSoft(); renderSide(); toast("Crawl analysis re-run: link scores, duplicates, chains, hreflang and sitemap checks updated"); } catch (e) { toast(e.message, "warn"); }
  });

  let lastState = "idle", lastCrawled = -1;
  let offlineSince = 0, lastStatusJson = "", pollTimer = null;
  const setText = (el, v) => { if (el.textContent !== v) el.textContent = v; };
  const setAttr = (el, k, v) => { if (el[k] !== v) el[k] = v; };
  async function poll() {
    try { S.status = await api("/api/crawl/status"); if (offlineSince) { offlineSince = 0; $("#offline").classList.add("hidden"); toast("Reconnected to the app server"); } }
    catch (_) { if (!offlineSince) offlineSince = Date.now(); if (Date.now() - offlineSince > 3000) $("#offline").classList.remove("hidden"); return; }
    const s = S.status, running = s.state === "running" || s.state === "paused" || s.state === "stopping";
    // nothing changed since the last tick: no DOM writes at all (each write costs a style/layout pass in WebView2)
    const sj = JSON.stringify(s);
    if (sj === lastStatusJson && s.state === lastState) { schedulePoll(running); return; }
    lastStatusJson = sj;
    S.hasCrawl = !!s.db;
    [["#btn-export", "Export the current filter or the whole crawl"], ["#btn-reports", "Reports for the open crawl"], ["#btn-analysis", "Re-run crawl analysis"], ["#btn-fixai", "Generate AI fix proposals for this filter"]].forEach(([sel, t]) => {
      const b = $(sel); setAttr(b, "disabled", !S.hasCrawl); setAttr(b, "title", S.hasCrawl ? t : "Open or start a crawl first");
    });
    refreshStartState();
    setAttr($("#btn-stop"), "disabled", !running); setAttr($("#btn-pause"), "disabled", !running || s.state === "stopping");
    setAttr($("#btn-pause"), "title", running ? (s.state === "paused" ? "Continue crawling" : "Pause; the crawl can be resumed") : "Nothing is crawling");
    setText($("#btn-pause"), s.state === "paused" ? "Resume" : "Pause");
    const pct = s.total ? Math.round(100 * s.crawled / s.total) : 0;
    const fill = $("#progress-fill"); if (fill.style.width !== `${pct}%`) fill.style.width = `${pct}%`;
    $("#progress-bar").classList.toggle("indeterminate", s.state === "stopping" || !!s.analysis_running || (s.state === "running" && !s.total));
    const label = { idle: "Idle", running: "Crawling", paused: "Paused", stopping: "Stopping…", finished: "Crawl complete", stopped: "Crawl stopped", error: "Error" }[s.state] || s.state;
    const qb = s.queue && Object.keys(s.queue).length ? ` · remaining: ${fmtNum(s.queue.pages)} pages, ${fmtNum(s.queue.images)} images, ${fmtNum(s.queue.resources)} JS/CSS, ${fmtNum(s.queue.external)} external` : "";
    setText($("#progress-text"), s.state === "idle" ? "Idle. Enter a URL and press Start." :
      `${label} — ${fmtNum(s.crawled)} of ${fmtNum(s.total)} URLs (${pct}%) · ${fmtNum(s.pages || 0)} HTML pages crawled${qb} · ${s.rate} URL/s · ${Math.round(s.elapsed)}s${s.analysis_running ? " · analysing…" : ""}${s.error ? " · " + s.error : ""}`);
    if (running && s.crawled !== lastCrawled) {
      lastCrawled = s.crawled;
      if (Date.now() - lastRefresh > 4000) { lastRefresh = Date.now(); await loadTabs(); refreshTableSoft(); if (S.sidePane === "issues") renderSide(); }
    }
    if (lastState !== s.state && (s.state === "finished" || s.state === "stopped" || s.state === "error")) {
      await loadTabs(); refreshTableSoft(); renderSide();
      if (lastState !== "idle") toast(s.state === "error" ? `Crawl error: ${s.error}` : `${label}: ${fmtNum(s.crawled)} URLs in ${Math.round(s.elapsed)}s`, s.state === "error" ? "error" : "info");
      if (lastState !== "idle" && s.state !== "error") setTimeout(autoCompare, 1500);
      if (lastState !== "idle" && s.state !== "error") setTimeout(async () => { try { const a = await api("/api/audit"); toast(`Site Health ${Math.round(a.health)} / 100 · ${fmtNum(a.tally.issue.instances)} errors · ${fmtNum(a.tally.warning.instances)} warnings. Open Audit for the breakdown.`, a.health >= 80 ? "info" : "warn", 9000); } catch (_) {} }, 2500);
    }
    lastState = s.state;
    schedulePoll(running);
  }
  let lastRefresh = 0;
  // 1s while crawling, 2.5s otherwise; the server is polled, the DOM is only written when something changed
  function schedulePoll(running) { clearTimeout(pollTimer); pollTimer = setTimeout(poll, running ? 1000 : 2500); }
  poll();

  // ------------------------------------------------------------------ Google Search Console + GA4
  async function wireGoogle(root) {
    const btn = $("#cfg-g-connect", root); if (!btn) return;
    const out = $("#cfg-g-out", root), dis = $("#cfg-g-disconnect", root);
    const fill = (sel, items, current, autoLabel) => { if (!sel) return; sel.innerHTML = (autoLabel ? `<option value="">${autoLabel}</option>` : `<option value="">— not set —</option>`) + items.map((i) => `<option value="${esc(i.value)}" ${i.value === current ? "selected" : ""}>${esc(i.label)}</option>`).join(""); if (current && !items.some((i) => i.value === current)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(current)}" selected>${esc(current)}</option>`); };
    const refresh = async () => {
      let st; try { st = await api("/api/google/status"); } catch (e) { out.textContent = e.message; return; }
      dis.classList.toggle("hidden", !st.connected);
      if (!st.configured) { out.textContent = "Paste the OAuth client ID and secret, then Save, then Connect."; btn.disabled = true; return; }
      btn.disabled = false; btn.textContent = st.connected ? "Reconnect Google account" : "Connect Google account";
      if (!st.connected) { out.textContent = "Not connected."; return; }
      out.innerHTML = `<span class="spinner"></span> Connected as <b>${esc(st.account)}</b> · loading properties…`;
      try {
        const p = await api("/api/google/properties");
        fill($("[data-g-prop='gsc_property']", root), p.gsc_sites.map((s) => ({ value: s.url, label: `${s.url} (${s.permission})` })), S.config.gsc_property, `Auto${p.suggested_gsc ? ` → ${p.suggested_gsc}` : " (match the crawled site)"}`);
        fill($("[data-g-prop='ga4_property']", root), p.ga4_properties.map((x) => ({ value: x.id, label: `${x.name} · ${x.id}` })), S.config.ga4_property, "");
        out.className = "field-ok"; out.innerHTML = `${ic("check")}Connected as <b>${esc(st.account)}</b> · ${p.gsc_sites.length} Search Console propert${p.gsc_sites.length === 1 ? "y" : "ies"}, ${p.ga4_properties.length} GA4 propert${p.ga4_properties.length === 1 ? "y" : "ies"}${p.ga4_error ? ` · GA4: ${esc(p.ga4_error)}` : ""}`;
      } catch (e) { out.className = "field-err"; out.innerHTML = ic("x") + esc(e.message); }
    };
    btn.onclick = () => busy(btn, async () => {
      try {
        const r = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readConfig()) });
        if (r.status === 422) { showConfigErrors((await r.json()).errors || {}); return; }
        cfgLoaded = JSON.stringify(readConfig());
        const a = await api("/api/google/auth-url");
        window.open(a.url, "_blank", "noopener");
        out.className = "muted"; out.innerHTML = `<span class="spinner"></span> Finish the sign-in in the browser window… (redirects to ${esc(a.redirect_uri)})`;
        const t0 = Date.now(); const poll = async () => { const st = await api("/api/google/status"); if (st.connected && st.account) { await refresh(); toast(`Google connected as ${st.account}`); return; } if (Date.now() - t0 < 180000) setTimeout(poll, 2000); else out.textContent = "Sign-in not completed. Try again."; }; setTimeout(poll, 3000);
      } catch (e) { out.className = "field-err"; out.innerHTML = ic("x") + esc(e.message); }
    });
    dis.onclick = async () => { await post("/api/google/disconnect"); toast("Google account disconnected"); refresh(); };
    refresh();
  }
  let gTimer = null;
  async function pollGoogle() {
    try {
      const st = await api("/api/google/status"); const j = st.job || {};
      if (j.state === "running") { if (!gTimer) gTimer = setInterval(pollGoogle, 2500); $("#btn-google").innerHTML = `${ic("cloud")}Data · ${esc(j.step || "…")}${j.total ? ` ${j.done}/${j.total}` : ""}${ic("chevron-down", 14, "caret")}`; }
      else if (gTimer) {
        clearInterval(gTimer); gTimer = null; $("#btn-google").innerHTML = `${ic("cloud")}Data${ic("chevron-down", 14, "caret")}`;
        const r = j.result || {}; const parts = []; if (r.gsc) parts.push(`Search Console: ${fmtNum(r.gsc.matched)} pages matched, ${fmtNum(r.gsc.orphans)} orphans`); if (r.ga4) parts.push(`GA4: ${fmtNum(r.ga4.matched)} matched, ${fmtNum(r.ga4.orphans)} orphans`); if (r.inspect) parts.push(`${fmtNum(r.inspect.inspected)} URLs inspected`);
        toast(j.state === "error" ? `Google fetch failed: ${(j.errors || []).join("; ")}` : `${parts.join(" · ") || "Google data fetched"}${j.errors && j.errors.length ? ` · ${j.errors.length} warning(s)` : ""} — see the Search Console and Analytics tabs`, j.state === "error" ? "error" : "info", 9000);
        await loadTabs(); refreshTableSoft(); if (S.selectedId) showDetail(S.selectedId);
      }
    } catch (_) {}
  }
  let lmTimer = null;
  async function pollLinkMetrics() {
    try {
      const s = await api("/api/linkmetrics/status");
      if (s.state === "running") { if (!lmTimer) lmTimer = setInterval(pollLinkMetrics, 2500); }
      else if (lmTimer) { clearInterval(lmTimer); lmTimer = null; toast(s.state === "error" ? `Link metrics failed: ${(s.errors || []).join("; ")}` : `Link metrics: ${fmtNum(s.done)} URLs from ${(s.providers || []).join(", ")}${s.error_count ? ` · ${s.error_count} error(s): ${s.errors[0] || ""}` : ""} — see the Link Metrics tab`, s.state === "error" ? "error" : s.error_count ? "warn" : "info", 8000); await loadTabs(); refreshTableSoft(); if (S.selectedId) showDetail(S.selectedId); }
    } catch (_) {}
  }
  $$("#google-menu a[data-lm]").forEach((a) => a.onclick = async (e) => {
    e.preventDefault(); const t = a.dataset.lm;
    if (t === "config") { $("#btn-config").onclick(null, "Link metrics"); return; }
    if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; }
    const body = t === "selected" ? { ids: S.table ? S.table.getSelectedData().map((r) => r.id) : [] } : t === "filter" ? { key: S.filter } : { sample: 0 };
    if (t === "selected" && !body.ids.length) { toast("Select rows first (Ctrl/Shift-click)", "warn"); return; }
    try { const r = await post("/api/linkmetrics/run", body); toast(`Fetching link metrics for ${fmtNum(r.urls)} URLs from ${r.providers.join(", ")}…`); pollLinkMetrics(); }
    catch (err) { toast(err.message, "error", 8000); if (/API key/.test(err.message)) $("#btn-config").onclick(null, "Link metrics"); }
  });
  $$("#google-menu a[data-g]").forEach((a) => a.onclick = async (e) => {
    e.preventDefault(); const t = a.dataset.g;
    if (t === "config") { $("#btn-config").onclick(null, "Search Console & GA4"); return; }
    if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; }
    try { const r = await post("/api/google/run", { gsc: true, ga4: true, inspect: t === "inspect" ? (S.config.gsc_inspect_max || 50) : 0 }); toast(`Fetching Search Console + GA4 data${r.inspect ? ` and inspecting up to ${r.inspect} URLs` : ""}…`); pollGoogle(); }
    catch (err) { toast(err.message, "error", 8000); if (/Connect/.test(err.message)) $("#btn-config").onclick(null, "Search Console & GA4"); }
  });
  setTimeout(pollGoogle, 3000);

  // ------------------------------------------------------------------ Lighthouse / PageSpeed
  let lhTimer = null;
  async function runLighthouse(mode, ids) {
    if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; }
    const body = mode === "ids" ? { ids } : mode === "filter" ? { key: S.filter, max_pages: 50 } : { sample: 0 };
    try {
      const r = await post("/api/lighthouse/run", body);
      toast(`Lighthouse: auditing ${fmtNum(r.pages)} page${r.pages > 1 ? "s" : ""} on ${r.device} via ${r.source === "psi" ? "PageSpeed Insights" : "local Chrome"} (~${Math.round(r.pages * 25 / Math.max(1, S.config.lighthouse_concurrency || 2))} s)…`);
      pollLighthouse();
    } catch (e) { toast(e.message, "error", 8000); }
  }
  $$("#lh-menu a").forEach((a) => a.onclick = async (e) => {
    e.preventDefault(); const t = a.dataset.lh;
    if (t === "cancel") { await post("/api/lighthouse/cancel"); toast("Cancelling after the pages in progress finish"); return; }
    if (t === "selected") { const ids = S.table ? S.table.getSelectedData().map((r) => r.id) : []; if (!ids.length) { toast("Select rows first (Ctrl/Shift-click)", "warn"); return; } runLighthouse("ids", ids); return; }
    runLighthouse(t);
  });
  async function pollLighthouse() {
    try {
      const s = await api("/api/lighthouse/status");
      const pill = $("#lh-count");
      if (s.state === "running") { pill.textContent = `${s.done}/${s.total}`; pill.classList.remove("hidden"); if (!lhTimer) lhTimer = setInterval(pollLighthouse, 3000); }
      else {
        if (lhTimer) { clearInterval(lhTimer); lhTimer = null; toast(`Lighthouse ${s.state}: ${fmtNum(s.ok)} page${s.ok === 1 ? "" : "s"} audited${s.error_count ? `, ${s.error_count} failed (${s.errors[0] || ""})` : ""} — see the PageSpeed and Accessibility tabs`, s.error_count ? "warn" : "info", 8000); await loadTabs(); refreshTableSoft(); if (S.selectedId) showDetail(S.selectedId); }
        pill.classList.toggle("hidden", !s.audited); pill.textContent = s.audited ? fmtNum(s.audited) : "";
      }
    } catch (_) {}
  }
  setTimeout(pollLighthouse, 2500);

  // ------------------------------------------------------------------ visualisations (D3)
  let vizKind = "tree", vizSim = null;
  const vizColor = (d) => d.folder ? "var(--accent)" : (d.status >= 400 || !d.status) ? "var(--issue)" : d.status >= 300 ? "var(--warning)" : d.indexable ? "var(--ok)" : "var(--muted)";
  function vizTip(ev, d) {
    const tip = $("#viz-tip"); const r = $("#viz-body").getBoundingClientRect();
    tip.innerHTML = d.folder ? `<b>${esc(d.path)}</b><div class="muted">${fmtNum(d.pages)} pages</div>` : `<b>${esc(d.url)}</b><div class="muted">${d.status ?? "no response"} · ${d.indexable ? "indexable" : "non-indexable"} · ${fmtNum(d.inlinks)} inlinks · depth ${d.depth}</div>${d.title ? `<div>${esc(d.title)}</div>` : ""}`;
    tip.classList.remove("hidden"); tip.style.left = `${Math.min(ev.clientX - r.left + 14, r.width - 440)}px`; tip.style.top = `${ev.clientY - r.top + 14}px`;
  }
  const vizHide = () => $("#viz-tip").classList.add("hidden");
  function vizOpenNode(d) { if (d.folder || !d.url) return; closeModal("modal-viz"); $("#search").value = d.url; S.q = d.url; selectTab("internal", "internal:html"); toast(`Filtered the grid to ${d.url}`); }
  $("#btn-viz").onclick = () => { if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; } openModal("modal-viz"); renderViz(); };
  $$("#viz-tabs button").forEach((b) => b.onclick = () => { vizKind = b.dataset.v; $$("#viz-tabs button").forEach((x) => x.classList.toggle("active", x === b)); renderViz(); });
  $("#viz-limit").onchange = () => renderViz();
  $("#viz-export").onclick = () => {
    const svg = $("#viz-svg").cloneNode(true); svg.setAttribute("xmlns", "http://www.w3.org/2000/svg"); svg.setAttribute("width", $("#viz-svg").clientWidth); svg.setAttribute("height", $("#viz-svg").clientHeight);
    const css = getComputedStyle(document.documentElement); ["--ok", "--muted", "--warning", "--issue", "--accent", "--text", "--border-strong", "--panel", "--bg"].forEach((v) => { svg.innerHTML = svg.innerHTML.split(`var(${v})`).join(css.getPropertyValue(v).trim()); });
    const blob = new Blob([svg.outerHTML], { type: "image/svg+xml" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${vizKind}-${(S.config.start_url || "crawl").replace(/^https?:\/\//, "").replace(/[^a-z0-9.]+/gi, "_")}.svg`; a.click();
  };
  async function renderViz() {
    if (vizSim) { vizSim.stop(); vizSim = null; }
    const svgEl = $("#viz-svg"), svg = d3.select(svgEl); svg.selectAll("*").remove(); vizHide();
    const W = svgEl.clientWidth || 1200, H = svgEl.clientHeight || 700;
    $("#viz-info").textContent = "Loading…"; $("#viz-limit-wrap").classList.toggle("hidden", vizKind === "anchor" || vizKind === "body");
    const limit = Number($("#viz-limit").value);
    try {
      if (vizKind === "anchor" || vizKind === "body") {
        const d = await api(`/api/viz/words?kind=${vizKind}&limit=150`);
        $("#viz-info").textContent = `${fmtNum(d.distinct)} distinct words in ${fmtNum(d.total_words)} ${d.unit === "links" ? "anchor words" : "body words"} · size = frequency`;
        if (!d.words.length) { svg.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").attr("fill", "var(--muted)").text("No words found"); return; }
        const max = d.words[0].count, scale = d3.scaleSqrt().domain([1, max]).range([11, Math.min(90, H / 7)]);
        const g = svg.append("g").attr("transform", `translate(${W / 2},${H / 2})`);
        d3.layout.cloud().size([W - 40, H - 60]).words(d.words.map((w) => ({ text: w.text, size: scale(w.count), count: w.count }))).padding(3).rotate(() => 0).font("IBM Plex Sans, sans-serif").fontSize((w) => w.size)
          .on("end", (words) => {
            g.selectAll("text").data(words).join("text").style("font-size", (w) => `${w.size}px`).style("font-family", "IBM Plex Sans, sans-serif").style("font-weight", (w) => w.size > 30 ? 600 : 400)
              .attr("fill", (w, i) => ["var(--accent)", "var(--text)", "var(--ok)", "var(--muted)"][i % 4]).attr("text-anchor", "middle").attr("transform", (w) => `translate(${w.x},${w.y})`).text((w) => w.text)
              .on("mousemove", (ev, w) => { const tip = $("#viz-tip"); const r = $("#viz-body").getBoundingClientRect(); tip.innerHTML = `<b>${esc(w.text)}</b> · ${fmtNum(w.count)}`; tip.classList.remove("hidden"); tip.style.left = `${ev.clientX - r.left + 14}px`; tip.style.top = `${ev.clientY - r.top + 14}px`; }).on("mouseleave", vizHide)
              .on("click", (ev, w) => { closeModal("modal-viz"); $("#search").value = w.text; S.q = w.text; selectTab("internal", "internal:html"); });
          }).start();
        return;
      }
      const mode = vizKind.startsWith("dir") ? "directory" : "crawl";
      const d = await api(`/api/viz/tree?mode=${mode}&max_nodes=${limit}`);
      if (!d.tree) { svg.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").attr("fill", "var(--muted)").text("No HTML pages in this crawl"); $("#viz-info").textContent = ""; return; }
      $("#viz-info").textContent = mode === "crawl" ? `${fmtNum(d.nodes)} of ${fmtNum(d.total)} pages reachable by links from the start page${d.orphans ? ` · ${fmtNum(d.orphans)} not reachable (orphans / outside the first ${fmtNum(limit)})` : ""} · ${d.max_depth} levels${d.truncated ? " · showing the first " + fmtNum(limit) + " by inlinks" : ""} · scroll to zoom, drag to pan, click a node to filter the grid`
        : `${fmtNum(d.total)} pages in folders · click a page to filter the grid`;
      const root = d3.hierarchy(d.tree);
      const g = svg.append("g");
      svg.call(d3.zoom().scaleExtent([0.1, 6]).on("zoom", (ev) => g.attr("transform", ev.transform)));
      const nodes = root.descendants(), links = root.links();
      const r = (n) => n.data.folder ? Math.max(4, Math.min(16, 3 + Math.sqrt(n.data.pages || 1) * 1.5)) : Math.max(3, Math.min(12, 3 + Math.sqrt(n.data.inlinks || 0) / 2));
      if (vizKind === "tree" || vizKind === "dirtree") {
        const leaves = root.leaves().length;
        const layout = d3.tree().nodeSize([14, 180]);
        layout(root);
        let minX = Infinity, maxX = -Infinity; nodes.forEach((n) => { minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x); });
        const scale = Math.min(1, (H - 60) / Math.max(1, maxX - minX + 40));
        g.attr("transform", `translate(40,${(H - (maxX - minX) * scale) / 2 - minX * scale}) scale(${scale})`);
        svg.call(d3.zoom().scaleExtent([0.05, 6]).on("zoom", (ev) => g.attr("transform", ev.transform)).transform, d3.zoomIdentity.translate(40, (H - (maxX - minX) * scale) / 2 - minX * scale).scale(scale));
        g.selectAll("path.link").data(links).join("path").attr("class", "link").attr("d", d3.linkHorizontal().x((n) => n.y).y((n) => n.x));
        const node = g.selectAll("g.node").data(nodes).join("g").attr("class", (n) => "node" + (n.data.folder ? " folder" : "")).attr("transform", (n) => `translate(${n.y},${n.x})`);
        node.append("circle").attr("r", (n) => r(n)).attr("fill", (n) => vizColor(n.data)).on("mousemove", (ev, n) => vizTip(ev, n.data)).on("mouseleave", vizHide).on("click", (ev, n) => vizOpenNode(n.data));
        if (nodes.length <= 400) node.append("text").attr("dx", (n) => r(n) + 4).attr("dy", 4).text((n) => (n.data.name || "").slice(0, 40));
      } else {
        const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).distance((l) => 18 + r(l.target) + r(l.source)).strength(0.9)).force("charge", d3.forceManyBody().strength(-60)).force("center", d3.forceCenter(W / 2, H / 2)).force("collide", d3.forceCollide((n) => r(n) + 2));
        vizSim = sim;
        const link = g.selectAll("line").data(links).join("line").attr("class", "link");
        const node = g.selectAll("g.node").data(nodes).join("g").attr("class", (n) => "node" + (n.data.folder ? " folder" : ""))
          .call(d3.drag().on("start", (ev, n) => { if (!ev.active) sim.alphaTarget(0.3).restart(); n.fx = n.x; n.fy = n.y; }).on("drag", (ev, n) => { n.fx = ev.x; n.fy = ev.y; }).on("end", (ev, n) => { if (!ev.active) sim.alphaTarget(0); n.fx = null; n.fy = null; }));
        node.append("circle").attr("r", (n) => r(n)).attr("fill", (n) => vizColor(n.data)).on("mousemove", (ev, n) => vizTip(ev, n.data)).on("mouseleave", vizHide).on("click", (ev, n) => vizOpenNode(n.data));
        if (nodes.length <= 150) node.append("text").attr("dx", (n) => r(n) + 3).attr("dy", 4).text((n) => (n.data.name || "").slice(0, 30));
        sim.on("tick", () => { link.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y); node.attr("transform", (n) => `translate(${n.x},${n.y})`); });
      }
    } catch (e) { $("#viz-info").textContent = e.message; }
  }

  // ------------------------------------------------------------------ XML sitemap generation
  const smQuery = () => new URLSearchParams(Object.fromEntries($$("#sm-opts [data-o]").map((i) => [i.dataset.o, i.checked ? "true" : "false"]))).toString();
  let smTimer = null;
  async function smPreview() {
    const box = $("#sm-preview"); box.className = "cfg-warn"; box.innerHTML = `<span class="spinner"></span> Counting…`;
    try {
      const p = await api(`/api/sitemap/preview?${smQuery()}`);
      const ex = Object.entries(p.excluded).filter(([, n]) => n).map(([k, n]) => `${fmtNum(n)} ${{ non_2xx: "non-2xx", noindex: "noindex", canonicalised: "canonicalised", paginated: "paginated" }[k] || k}`).join(", ");
      box.className = "cfg-warn " + (p.urls ? "ok" : ""); box.innerHTML = `<b>${fmtNum(p.urls)} URLs</b> will be included${p.files > 1 ? ` in ${p.files} files + sitemap_index.xml (zip)` : ""}${ex ? ` · excluded: ${ex}` : ""}${p.sample.length ? `<br><span class="muted">${esc(p.sample.slice(0, 3).join("  ·  "))}${p.urls > 3 ? "  …" : ""}</span>` : ""}`;
      $("#sm-download").innerHTML = ic("download") + (p.files > 1 ? "Download sitemaps (zip)" : "Download sitemap.xml"); $("#sm-download").href = `/api/sitemap/generate?${smQuery()}`;
    } catch (e) { box.className = "cfg-warn"; box.textContent = e.message; }
  }
  function openSitemap() {
    if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; }
    openModal("modal-sitemap"); smPreview();
    $$("#sm-opts [data-o]").forEach((i) => i.onchange = () => { clearTimeout(smTimer); smTimer = setTimeout(smPreview, 150); });
  }
  $("#sm-download").onclick = () => setTimeout(() => toast("Sitemap downloading. Upload it to the site root and submit it in Search Console."), 300);

  // ------------------------------------------------------------------ site audit dashboard
  const scClass = (v) => v == null ? "" : v >= 80 ? "sc-good" : v >= 60 ? "sc-fair" : "sc-poor";
  const scColor = (v) => v == null ? "var(--muted)" : v >= 80 ? "var(--ok)" : v >= 60 ? "var(--warning)" : "var(--issue)";
  $("#btn-audit").onclick = () => { if (!S.hasCrawl) { toast("Open or start a crawl first", "warn"); return; } openModal("modal-audit"); loadAudit(); };
  $("#audit-refresh").onclick = () => loadAudit();
  async function loadAudit() {
    $("#audit-body").innerHTML = skel.kv(8);
    try {
      const [a, h] = await Promise.all([api("/api/audit"), api("/api/audit/history").catch(() => ({ points: [] }))]);
      S.audit = a; renderAudit(a, h.points || []);
    } catch (e) { $("#audit-body").innerHTML = empty("!", "Could not build the audit", e.message); }
  }
  function renderAudit(a, points) {
    $("#audit-site").textContent = `${a.site || ""} · ${(a.finished_at || a.started_at || "").replace("T", " ").slice(0, 16)} · ${fmtNum(a.pages.html)} HTML pages`;
    const r = 64, circ = 2 * Math.PI * r, dash = circ * (a.health / 100);
    const ring = `<div class="audit-ring"><svg viewBox="0 0 150 150"><circle cx="75" cy="75" r="${r}" fill="none" stroke="var(--head)" stroke-width="12"/><circle cx="75" cy="75" r="${r}" fill="none" stroke="${scColor(a.health)}" stroke-width="12" stroke-linecap="round" stroke-dasharray="${dash} ${circ - dash}"/></svg><div class="v"><b class="${scClass(a.health)}">${Math.round(a.health)}</b><span>Site Health</span></div></div>`;
    const t = a.tally;
    const tally = `<div class="audit-tally"><div><b class="sc-poor">${fmtNum(t.issue.instances)}</b><small>Errors · ${t.issue.types} types</small></div><div><b class="sc-fair">${fmtNum(t.warning.instances)}</b><small>Warnings · ${t.warning.types} types</small></div><div><b>${fmtNum(t.opportunity.instances)}</b><small>Notices · ${t.opportunity.types} types</small></div></div>`;
    const p = a.pages, tot = Math.max(1, p.html + p.blocked);
    const seg = (n, c) => n ? `<i style="width:${100 * n / tot}%;background:${c}" title="${fmtNum(n)}"></i>` : "";
    const pages = `<div class="audit-pages"><div class="muted" style="font-size:var(--fs-xs);text-align:left"><b>${fmtNum(p.html)}</b> HTML pages crawled · ${fmtNum(a.indexable)} indexable</div>
      <div class="audit-bar">${seg(p.healthy, "var(--ok)")}${seg(p.with_issues, "var(--warning)")}${seg(p.redirects, "var(--accent)")}${seg(p.broken, "var(--issue)")}${seg(p.blocked, "var(--muted)")}</div>
      <div class="audit-legend"><span><i style="background:var(--ok)"></i>${fmtNum(p.healthy)} healthy</span><span><i style="background:var(--warning)"></i>${fmtNum(p.with_issues)} with errors</span><span><i style="background:var(--accent)"></i>${fmtNum(p.redirects)} redirects</span><span><i style="background:var(--issue)"></i>${fmtNum(p.broken)} broken</span><span><i style="background:var(--muted)"></i>${fmtNum(p.blocked)} blocked</span></div></div>`;
    const themes = a.themes.map((th) => `<div class="audit-theme" data-theme="${th.id}" title="${esc(th.desc)}"><div class="h"><b>${esc(th.name)}</b><span class="sc ${scClass(th.score)}">${th.score == null ? "n/a" : Math.round(th.score)}</span></div><div class="bar"><i style="width:${th.score == null ? 0 : th.score}%;background:${scColor(th.score)}"></i></div>
      ${th.top.length ? `<ul>${th.top.map((i) => `<li><a href="#" data-filter="${esc(i.key)}">${esc(i.name)}</a> <span>${fmtNum(i.count)}</span></li>`).join("")}</ul>` : `<div class="muted" style="font-size:var(--fs-xs)">${th.score == null ? (th.id === "cwv" || th.id === "performance" || th.id === "accessibility" ? "Run PageSpeed to score this" : "No data in this crawl") : "No issues"}</div>`}</div>`).join("");
    const cwv = a.cwv ? `<div class="audit-section">Core Web Vitals <span>${fmtNum(a.cwv.audited)} pages audited with Lighthouse${a.cwv.field ? ` · ${fmtNum(a.cwv.field)} with CrUX field data` : ""}</span></div>
      <div class="tiles"><div class="tile"><div class="v ${scClass(a.cwv.perf_avg)}">${a.cwv.perf_avg ?? "—"}</div><div class="l">Avg performance</div></div><div class="tile"><div class="v">${fmtNum(a.cwv.lcp_good)}/${fmtNum(a.cwv.audited)}</div><div class="l">LCP ≤ 2.5 s</div></div><div class="tile"><div class="v">${fmtNum(a.cwv.cls_good)}/${fmtNum(a.cwv.audited)}</div><div class="l">CLS ≤ 0.1</div></div><div class="tile"><div class="v">${fmtNum(a.cwv.tbt_good)}/${fmtNum(a.cwv.audited)}</div><div class="l">TBT ≤ 200 ms</div></div><div class="tile"><div class="v ${scClass(a.cwv.a11y_avg)}">${a.cwv.a11y_avg ?? "—"}</div><div class="l">Avg accessibility</div></div></div>` : "";
    const trend = points.length > 1 ? (() => {
      const W = 600, H = 110, pad = 24, xs = points.map((_, i) => pad + i * (W - 2 * pad) / (points.length - 1)), y = (v) => H - 10 - (v / 100) * (H - 24);
      const path = points.map((pt, i) => `${i ? "L" : "M"}${xs[i]},${y(pt.health)}`).join(" ");
      return `<div class="audit-section">Progress <span>Site Health across ${points.length} crawls of this site</span></div><svg class="audit-trend" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line x1="${pad}" y1="${y(80)}" x2="${W - pad}" y2="${y(80)}" stroke="var(--ok)" stroke-dasharray="3 4" opacity=".5"/><line x1="${pad}" y1="${y(60)}" x2="${W - pad}" y2="${y(60)}" stroke="var(--warning)" stroke-dasharray="3 4" opacity=".5"/>
        <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>${points.map((pt, i) => `<circle cx="${xs[i]}" cy="${y(pt.health)}" r="${pt.current ? 5 : 3.5}" fill="${scColor(pt.health)}"><title>${esc((pt.at || "").slice(0, 16))}: health ${pt.health}, ${fmtNum(pt.errors)} errors, ${fmtNum(pt.pages)} pages</title></circle><text x="${xs[i]}" y="${y(pt.health) - 8}" text-anchor="middle" font-size="10" fill="var(--text)">${Math.round(pt.health)}</text>`).join("")}
        ${points.map((pt, i) => `<text x="${xs[i]}" y="${H - 1}" text-anchor="middle" font-size="9" fill="var(--muted)">${esc((pt.at || "").slice(5, 16).replace("T", " "))}</text>`).join("")}</svg>` })() : `<div class="audit-section">Progress <span>crawl this site again to see the trend</span></div>`;
    const top = `<div class="audit-section">Top issues by impact on the score <span>cost in points; click to open the filter</span></div><table class="audit-top"><tr><th>Issue</th><th>Type</th><th>Priority</th><th>Pages</th><th>Cost</th></tr>${a.top_issues.map((i) => `<tr><td><a href="#" data-filter="${esc(i.key)}">${esc(i.tab_name)}: ${esc(i.name)}</a></td><td><span class="badge ${i.kind}"></span>${esc(i.kind)}</td><td>${esc(i.priority)}</td><td>${fmtNum(i.count)}</td><td class="pen">−${i.penalty}</td></tr>`).join("")}</table>`;
    $("#audit-body").innerHTML = `<div class="audit-grid"><div class="audit-health">${ring}${tally}${pages}</div><div><div class="audit-section" style="margin-top:0">Thematic scores <span>100 minus the weighted share of pages affected by each theme's issues</span></div><div class="audit-themes">${themes}</div></div></div>${cwv}${trend}${top}`;
    $$("#audit-body a[data-filter]", $("#audit-body")).forEach((x) => x.onclick = (e) => { e.preventDefault(); closeModal("modal-audit"); selectTab(x.dataset.filter.split(":")[0], x.dataset.filter); });
    $$(".audit-theme", $("#audit-body")).forEach((x) => x.onclick = (e) => { if (e.target.closest("a")) return; const th = a.themes.find((t) => t.id === x.dataset.theme); if (th && th.top.length) { closeModal("modal-audit"); selectTab(th.top[0].key.split(":")[0], th.top[0].key); } });
  }
  // deep link from Content Pilot: /?url=https://example.com[&start=1]
  (() => {
    const q = new URLSearchParams(location.search);
    const u = q.get("url"); if (!u) return;
    const inp = $("#start-url"); inp.value = u.startsWith("http") ? u : "https://" + u;
    history.replaceState(null, "", location.pathname);
    setTimeout(() => { refreshStartState(); if (q.get("start") === "1" && !$("#btn-start").disabled) $("#btn-start").click(); else toast(`Start URL set from Content Pilot: ${inp.value}`); }, 1500);
  })();

  // ------------------------------------------------------------------ scheduled crawls
  let schEditing = null;
  $("#help-schedules").onclick = async (e) => { e.preventDefault(); openModal("modal-schedules"); schEditing = null; await loadSchedules(); };
  $("#sch-new").onclick = () => { schEditing = { name: "", url: S.config.start_url || "", mode: "spider", frequency: "daily", time: "03:00", day: "mon", every_hours: 6, config: { max_urls: 0 }, export: "issues", export_format: "xlsx", out_dir: "", compare: true, lighthouse: 0, google: false, enabled: true }; renderSchedules(S.schedules || []); };
  async function loadSchedules() { $("#schedules-body").innerHTML = skel.list(4); try { const d = await api("/api/schedules"); S.schedules = d.schedules; S.schedulesRunning = d.running; renderSchedules(d.schedules); } catch (e) { $("#schedules-body").innerHTML = empty("!", "Could not load schedules", e.message); } }
  const schWhen = (s) => s.frequency === "hourly" ? `every ${s.every_hours} h` : s.frequency === "weekly" ? `weekly ${s.day} ${s.time}` : s.frequency === "once" ? `once ${(s.once_at || "").replace("T", " ")}` : `daily ${s.time}`;
  function renderSchedules(list) {
    const body = $("#schedules-body");
    const form = schEditing ? `<div class="sch-form" id="sch-form">
        <label>Name<input data-s="name" value="${esc(schEditing.name)}" placeholder="e.g. Weekly full crawl"></label>
        <label>Start URL<input data-s="url" value="${esc(schEditing.url)}" placeholder="https://www.example.com"></label>
        <label>Mode<select data-s="mode">${["spider", "sitemap", "list"].map((m) => `<option ${m === schEditing.mode ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label>Frequency<select data-s="frequency">${["daily", "weekly", "hourly", "once"].map((m) => `<option ${m === schEditing.frequency ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label>Time (daily / weekly)<input data-s="time" value="${esc(schEditing.time)}" placeholder="03:00"></label>
        <label>Day (weekly)<select data-s="day">${["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((m) => `<option ${m === schEditing.day ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label>Every N hours (hourly)<input data-s="every_hours" type="number" min="1" value="${schEditing.every_hours}"></label>
        <label>Run once at<input data-s="once_at" type="datetime-local" value="${esc(schEditing.once_at || "")}"></label>
        <label>Max URLs (0 = all)<input data-s="max_urls" type="number" min="0" value="${(schEditing.config || {}).max_urls || 0}"></label>
        <label>Export after crawl<select data-s="export">${[["issues", "Issues pack"], ["everything", "Everything"], ["none", "Nothing"]].map(([v, l]) => `<option value="${v}" ${v === schEditing.export ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Export format<select data-s="export_format">${["xlsx", "zip"].map((m) => `<option ${m === schEditing.export_format ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label>Export folder (blank = exports\scheduled)<input data-s="out_dir" value="${esc(schEditing.out_dir || "")}"></label>
        <label>Lighthouse top N pages (0 = off)<input data-s="lighthouse" type="number" min="0" value="${schEditing.lighthouse || 0}"></label>
        <label class="row"><input type="checkbox" data-s="compare" ${schEditing.compare ? "checked" : ""}> Compare with the previous crawl</label>
        <label class="row"><input type="checkbox" data-s="google" ${schEditing.google ? "checked" : ""}> Fetch Search Console + GA4 (needs a connected account)</label>
        <label class="row"><input type="checkbox" data-s="enabled" ${schEditing.enabled !== false ? "checked" : ""}> Enabled</label>
        <div class="full"><button type="button" class="primary" id="sch-save">Save schedule</button><button type="button" id="sch-cancel">Cancel</button><span class="muted">Email on finish follows Config → Email.</span></div></div>` : "";
    const rows = list.length ? `<table><tr><th>Schedule</th><th>When</th><th>Next run</th><th>Last run</th><th>After crawl</th><th></th></tr>${list.map((s) => {
      const h = (s.history || [])[0];
      const after = [s.export !== "none" ? `export ${s.export}` : "", s.compare ? "compare" : "", s.lighthouse ? `Lighthouse ${s.lighthouse}` : "", s.google ? "Google" : ""].filter(Boolean).join(", ");
      return `<tr class="${s.enabled === false ? "muted" : ""}"><td><b>${esc(s.name)}</b><div class="muted">${esc(s.url)} · ${esc(s.mode)}${(s.config || {}).max_urls ? ` · max ${fmtNum(s.config.max_urls)} URLs` : ""}</div></td><td>${esc(schWhen(s))}${s.enabled === false ? " (off)" : ""}</td><td>${esc((s.next_run || "—").replace("T", " "))}</td>
        <td>${h ? `<span class="${h.ok ? "lh-good" : "lh-poor"}">${h.ok ? ic("check") : ic("x")}</span> ${esc((h.started || "").replace("T", " ").slice(0, 16))}<div class="sch-hist">${h.ok ? `${h.urls != null ? `${fmtNum(h.urls)} URLs · ${fmtNum(h.issues || 0)} issue types` : "ran"}${h.compare && h.compare.issues_up != null ? ` · ${h.compare.issues_up} up / ${h.compare.issues_down} down` : ""}${h.export ? ` · <a href="#" title="${esc(h.export)}">exported</a>` : ""}` : esc(h.error || h.note || "")}</div>` : "—"}</td>
        <td class="muted">${esc(after || "—")}</td><td class="act"><button data-run="${s.id}" title="Run now" aria-label="Run now">${ic("play")}</button> <button data-edit="${s.id}" title="Edit" aria-label="Edit">${ic("pencil")}</button> <button data-del="${s.id}" title="Delete" aria-label="Delete">${ic("trash")}</button></td></tr>`; }).join("")}</table>`
      : (schEditing ? "" : empty("clock", "No schedules yet", "Click + New schedule. Each run crawls, exports, compares with the previous crawl and emails you if Config → Email is on."));
    body.innerHTML = form + rows;
    const save = $("#sch-save", body);
    if (save) save.onclick = () => busy(save, async () => {
      const f = $("#sch-form", body); const g = (k) => $(`[data-s="${k}"]`, f);
      const s = { ...schEditing, name: g("name").value.trim(), url: g("url").value.trim(), mode: g("mode").value, frequency: g("frequency").value, time: g("time").value.trim(), day: g("day").value, every_hours: Number(g("every_hours").value || 6), once_at: g("once_at").value,
        config: { ...(schEditing.config || {}), max_urls: Number(g("max_urls").value || 0) }, export: g("export").value, export_format: g("export_format").value, out_dir: g("out_dir").value.trim(), lighthouse: Number(g("lighthouse").value || 0), compare: g("compare").checked, google: g("google").checked, enabled: g("enabled").checked };
      if (!s.config.max_urls) delete s.config.max_urls;
      try { await post("/api/schedules", s); schEditing = null; toast("Schedule saved"); await loadSchedules(); } catch (e) { toast(e.message, "error"); }
    });
    const cancel = $("#sch-cancel", body); if (cancel) cancel.onclick = () => { schEditing = null; renderSchedules(S.schedules || []); };
    $$("[data-edit]", body).forEach((b) => b.onclick = () => { schEditing = { ...(S.schedules || []).find((x) => x.id === b.dataset.edit) }; renderSchedules(S.schedules || []); });
    $$("[data-del]", body).forEach((b) => b.onclick = async () => { if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Delete?"; setTimeout(() => { b.dataset.armed = ""; b.innerHTML = ic("trash"); }, 3000); return; } await fetch(`/api/schedules/${b.dataset.del}`, { method: "DELETE" }); toast("Schedule deleted"); loadSchedules(); });
    $$("[data-run]", body).forEach((b) => b.onclick = async () => { try { const r = await post(`/api/schedules/${b.dataset.run}/run`); closeModal("modal-schedules"); toast(`Running schedule "${r.started}" — the crawl starts now; exports and comparison follow when it finishes`); } catch (e) { toast(e.message, "error"); } });
  }

  // ------------------------------------------------------------------ usage & costs
  let usageTab = "cumulative";
  const money = (v, unpriced) => v == null ? "—" : `$${Number(v).toFixed(v >= 100 ? 0 : v >= 1 ? 2 : 4)}${unpriced ? "*" : ""}`;
  const dur = (s) => s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
  $("#help-usage").onclick = async (e) => { e.preventDefault(); openModal("modal-usage"); $("#usage-body").innerHTML = skel.kv(6); await loadUsage(); };
  $$("#modal-usage .cmp-tabs button").forEach((b) => b.onclick = () => { usageTab = b.dataset.u; $$("#modal-usage .cmp-tabs button").forEach((x) => x.classList.toggle("active", x === b)); renderUsage(); });
  async function loadUsage() {
    try { S.usage = await api("/api/usage"); } catch (err) { $("#usage-body").innerHTML = empty("!", "Could not load usage", err.message); return; }
    renderUsage();
  }
  function renderUsage() {
    const u = S.usage, body = $("#usage-body"); if (!u) return;
    const t = u.totals, L = u.by_model;
    const tile = (v, l) => `<div class="tile"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`;
    const unp = t.unpriced_models.length ? `<p class="usage-note">* ${t.unpriced_models.map(esc).join(", ")}: no price set, so the cost is a lower bound. Add prices in the Prices tab.</p>` : "";
    if (usageTab === "cumulative") {
      body.innerHTML = `<div class="tiles">${tile(fmtNum(t.crawls), "Crawls run")}${tile(fmtNum(t.urls), "URLs fetched")}${tile(fmtNum(t.pages), "HTML pages")}${tile(dur(t.crawl_seconds), "Crawl time")}${tile(fmtNum(t.ai_requests), "AI requests")}${tile(fmtNum(t.input_tokens), "Tokens in")}${tile(fmtNum(t.output_tokens), "Tokens out")}${tile(money(t.cost_usd, t.unpriced_models.length), "Est. AI cost")}</div>
        ${L.length ? `<table><tr><th>Provider</th><th>Model</th><th class="num">Requests</th><th class="num">Tokens in</th><th class="num">Tokens out</th><th class="num">$/M in</th><th class="num">$/M out</th><th class="num">Est. cost</th></tr>${L.map((m) => `<tr><td>${esc(m.provider)}</td><td>${esc(m.model)}</td><td class="num">${fmtNum(m.requests)}</td><td class="num">${fmtNum(m.input_tokens)}</td><td class="num">${fmtNum(m.output_tokens)}</td><td class="num">${m.price_in == null ? "—" : m.price_in}</td><td class="num">${m.price_out == null ? "—" : m.price_out}</td><td class="num">${money(m.priced ? m.cost_usd : null)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No AI requests yet. Costs appear here once you generate fixes.</div>`}
        ${unp}<p class="usage-note">Since ${esc((t.first_at || "").slice(0, 16).replace("T", " ") || "the first crawl")}. Stored locally in ${esc(u.path)}; token counts come from each provider's own response.</p>`;
    } else if (usageTab === "runs") {
      const rows = u.runs.map((r) => `<tr><td title="${esc(r.crawl)}">${esc((r.started_at || "").slice(0, 16).replace("T", " "))}</td><td>${esc(r.start_url)}</td><td>${esc(r.mode)}${r.rendering === "javascript" ? " · JS" : ""}</td><td class="num">${fmtNum(r.urls)}</td><td class="num">${fmtNum(r.pages)}</td><td class="num">${dur(r.elapsed)}</td><td class="num">${fmtNum(r.ai.requests)}</td><td class="num">${fmtNum(r.ai.input_tokens + r.ai.output_tokens)}</td><td class="num">${money(r.ai.requests ? r.ai.cost_usd : null, r.ai.unpriced)}</td></tr>`).join("");
      const other = Object.entries(u.other_ai || {}).filter(([k]) => k !== undefined);
      body.innerHTML = u.runs.length ? `<table><tr><th>Started</th><th>Site</th><th>Mode</th><th class="num">URLs</th><th class="num">Pages</th><th class="num">Time</th><th class="num">AI requests</th><th class="num">Tokens</th><th class="num">Est. cost</th></tr>${rows}</table>` : empty("trending-up", "No crawls recorded yet", "Runs are recorded from now on; older crawl files are not back-filled.");
      if (other.length) body.insertAdjacentHTML("beforeend", `<p class="usage-note">AI usage outside a recorded run (test connections, fixes on older crawls): ${other.map(([k, a]) => `${esc(k || "no crawl")} ${fmtNum(a.requests)} req · ${money(a.cost_usd, a.unpriced)}`).join(" · ")}</p>`);
      body.insertAdjacentHTML("beforeend", unp);
    } else {
      const keys = [...new Set([...Object.keys(u.prices), ...L.map((m) => `${m.provider}:${m.model}`)])].sort();
      body.innerHTML = `<p class="muted" style="margin:0 0 10px">USD per 1 million tokens. Anthropic rates are built in (official price list); fill in the others from your provider's pricing page. Blank both boxes to go back to the default.</p>
        <table id="price-table"><tr><th>Provider : model</th><th class="num">$/M input</th><th class="num">$/M output</th><th></th></tr>${keys.map((k) => { const p = u.prices[k] || [null, null], isDef = JSON.stringify(u.default_prices[k] || null) === JSON.stringify(u.prices[k] || null); return `<tr data-k="${esc(k)}"><td>${esc(k)}</td><td class="num"><input type="number" step="0.01" min="0" value="${p[0] ?? ""}" placeholder="—"></td><td class="num"><input type="number" step="0.01" min="0" value="${p[1] ?? ""}" placeholder="—"></td><td class="muted">${isDef && u.default_prices[k] ? "default" : u.prices[k] ? "custom" : "not set"}</td></tr>`; }).join("")}
        <tr data-k=""><td><input type="text" id="price-new" placeholder="provider:model, e.g. openai:gpt-4.1" style="width:260px"></td><td class="num"><input type="number" step="0.01" min="0" placeholder="in"></td><td class="num"><input type="number" step="0.01" min="0" placeholder="out"></td><td></td></tr></table>
        <div class="cfg-inline"><button type="button" class="primary" id="price-save">Save prices</button><span class="muted" id="price-out"></span></div>`;
      $("#price-save").onclick = () => busy($("#price-save"), async () => {
        const prices = {};
        $$("#price-table tr[data-k]").forEach((tr) => { const k = tr.dataset.k || ($("#price-new", tr) || {}).value || ""; if (!k.trim()) return; const [a, b] = $$("input[type=number]", tr).map((i) => i.value.trim()); prices[k.trim().toLowerCase()] = a === "" && b === "" ? null : [Number(a || 0), Number(b || 0)]; });
        try { await post("/api/usage/prices", { prices }); $("#price-out").textContent = "Saved"; await loadUsage(); } catch (err) { $("#price-out").textContent = err.message; }
      });
    }
  }

  // ------------------------------------------------------------------ compare (change detection)
  const crawlName = (p) => String(p || "").split(/[\\/]/).pop();
  async function fillCompareSelects() {
    const d = await api("/api/crawls");
    const done = d.crawls.filter((c) => c.state === "finished" || c.state === "stopped");
    const opt = (c) => `<option value="${esc(c.name)}">${esc(c.start_url || c.name)} — ${esc((c.started_at || c.modified || "").slice(0, 16).replace("T", " "))} · ${fmtNum(c.urls)} URLs</option>`;
    $("#cmp-a").innerHTML = done.map(opt).join(""); $("#cmp-b").innerHTML = done.map(opt).join("");
    const open = d.crawls.find((c) => c.open) || done[0];
    if (open) {
      $("#cmp-b").value = open.name;
      const host = (u) => (u || "").replace(/^https?:\/\//, "").split("/")[0];
      const prev = done.find((c) => c.name !== open.name && host(c.start_url) === host(open.start_url) && c.modified < open.modified);
      if (prev) $("#cmp-a").value = prev.name; else if (done[1]) $("#cmp-a").value = done.find((c) => c.name !== open.name)?.name || "";
    }
    return done.length;
  }
  $("#btn-compare").onclick = async () => {
    openModal("modal-compare");
    const n = await fillCompareSelects();
    if (n < 2) { $("#compare-body").innerHTML = empty("arrow-left-right", "Need two completed crawls", "Crawl the same site again later and compare the two here."); return; }
    runCompare();
  };
  $("#cmp-run").onclick = () => runCompare();
  let cmpTab = "issues";
  async function runCompare() {
    const a = $("#cmp-a").value, b = $("#cmp-b").value, body = $("#compare-body");
    if (!a || !b) return;
    if (a === b) { toast("Pick two different crawls", "warn"); return; }
    body.innerHTML = skel.kv(8);
    let r;
    try { r = await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&limit=2000`); } catch (e) { body.innerHTML = empty("!", "Could not compare", e.message); return; }
    $("#cmp-export").classList.remove("hidden"); $("#cmp-export").href = `/api/compare/export?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`;
    S.compare = r; renderCompare();
  }
  const dcls = (n) => n > 0 ? "delta-up" : n < 0 ? "delta-down" : "delta-0";
  const dtxt = (n) => n > 0 ? `+${fmtNum(n)}` : fmtNum(n);
  function renderCompare() {
    const r = S.compare, s = r.summary, body = $("#compare-body"), L = r.field_labels;
    const tile = (v, l, cls = "") => `<div class="tile"><div class="v ${cls}">${v}</div><div class="l">${esc(l)}</div></div>`;
    const tiles = tile(`${fmtNum(s.pages_before)} → ${fmtNum(s.pages_after)}`, "HTML pages") + tile(fmtNum(s.added), "Added", s.added ? "delta-up" : "") + tile(fmtNum(s.removed), "Removed", s.removed ? "delta-up" : "") +
      tile(fmtNum(s.changed), "Changed") + tile(fmtNum(s.issues_up), "Issues up", s.issues_up ? "delta-up" : "") + tile(fmtNum(s.issues_down), "Issues down", s.issues_down ? "delta-down" : "") +
      tile(fmtNum(s.issues_new), "New issues", s.issues_new ? "delta-up" : "") + tile(fmtNum(s.issues_fixed), "Fixed issues", s.issues_fixed ? "delta-down" : "");
    const pair = `<div class="cmp-pair"><b>A</b> ${esc(r.a.start_url)} · ${esc(r.a.started_at)} · ${fmtNum(r.a.total)} URLs &nbsp;→&nbsp; <b>B</b> ${esc(r.b.start_url)} · ${esc(r.b.started_at)} · ${fmtNum(r.b.total)} URLs${s.same_site ? "" : ' · <span class="delta-up">different sites</span>'}</div>`;
    const tabs = [["issues", `Issues (${fmtNum(r.issues.length)})`], ["changed", `Changed (${fmtNum(s.changed)})`], ["added", `Added (${fmtNum(s.added)})`], ["removed", `Removed (${fmtNum(s.removed)})`]];
    let table;
    if (cmpTab === "issues") table = r.issues.length ? `<table><tr><th>Issue</th><th>Type</th><th>Priority</th><th>Before</th><th>After</th><th>Change</th></tr>${r.issues.map((i) => `<tr><td><a href="#" data-filter="${esc(i.key)}">${esc(i.name)}</a></td><td>${esc(i.kind)}</td><td>${esc(i.priority)}</td><td>${fmtNum(i.before)}</td><td>${fmtNum(i.after)}</td><td class="${dcls(i.delta)}">${dtxt(i.delta)}</td></tr>`).join("")}</table>` : `<div class="pad muted">No issues in either crawl.</div>`;
    else if (cmpTab === "changed") {
      const fc = Object.entries(s.field_changes).filter(([, n]) => n).map(([f, n]) => `${esc(L[f] || f)} ${fmtNum(n)}`).join(" · ");
      table = (fc ? `<p class="muted" style="margin:0 0 8px">Pages with a changed ${fc}</p>` : "") + (r.changed.length ? `<table><tr><th>URL</th><th>Field</th><th>Before</th><th>After</th></tr>${r.changed.map((c) => Object.entries(c.changes).map(([f, v], i) => `<tr>${i === 0 ? `<td rowspan="${Object.keys(c.changes).length}"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url)}</a></td>` : ""}<td>${esc(L[f] || f)}</td><td class="old">${esc(v[0])}</td><td>${esc(v[1])}</td></tr>`).join("")).join("")}</table>` : `<div class="pad muted">No page changed in the compared fields.</div>`);
    } else {
      const list = cmpTab === "added" ? r.added : r.removed;
      table = list.length ? `<table>${list.map((u) => `<tr><td><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></td></tr>`).join("")}</table>` : `<div class="pad muted">None.</div>`;
    }
    const total = { issues: r.issues.length, changed: s.changed, added: s.added, removed: s.removed }[cmpTab];
    const shown = { issues: r.issues.length, changed: r.changed.length, added: r.added.length, removed: r.removed.length }[cmpTab];
    body.innerHTML = `<div class="tiles">${tiles}</div>${pair}<div class="cmp-tabs">${tabs.map(([k, l]) => `<button data-t="${k}" class="${k === cmpTab ? "active" : ""}">${l}</button>`).join("")}</div>${table}${shown < total ? `<p class="muted">Showing ${fmtNum(shown)} of ${fmtNum(total)} — the Excel export has everything.</p>` : ""}`;
    $$(".cmp-tabs button", body).forEach((b) => b.onclick = () => { cmpTab = b.dataset.t; renderCompare(); });
    $$("a[data-filter]", body).forEach((a) => a.onclick = (e) => { e.preventDefault(); closeModal("modal-compare"); selectTab(a.dataset.filter.split(":")[0], a.dataset.filter); });
  }
  async function autoCompare() {
    // after a crawl finishes: diff against the previous completed crawl of the same site, if there is one
    try {
      const r = await api("/api/compare?limit=1");
      const s = r.summary;
      toast(`Compared with the crawl from ${(r.a.started_at || "").slice(0, 16)}: ${s.issues_up} issue${s.issues_up === 1 ? "" : "s"} up, ${s.issues_down} down, ${fmtNum(s.changed)} pages changed, ${fmtNum(s.added)} added, ${fmtNum(s.removed)} removed. Open Compare for the details.`, s.issues_up || s.removed ? "warn" : "info", 9000);
    } catch (_) { /* no earlier crawl of this site */ }
  }

  // ------------------------------------------------------------------ menus, export, reports
  $$(".menu > button").forEach((b) => b.onclick = (e) => { e.stopPropagation(); const m = b.parentElement; const open = m.classList.contains("open"); $$(".menu").forEach((x) => x.classList.remove("open")); m.classList.toggle("open", !open); });
  document.addEventListener("click", () => $$(".menu").forEach((x) => x.classList.remove("open")));
  $$("#export-menu a").forEach((a) => a.onclick = async (e) => {
    e.preventDefault();
    const t = a.dataset.export, f = currentFilter();
    if (t === "centre") { openExportCentre(); return; }
    if (t === "sitemap") { openSitemap(); return; }
    if (t === "md") {
      try { const txt = await (await fetch("/api/export/issues.md")).text(); await navigator.clipboard.writeText(txt); toast("Issues summary copied as Markdown — paste it into a ticket or doc"); } catch (_) { window.location.href = "/api/export/issues.md"; }
      return;
    }
    if (t === "issue-inlinks" || t === "issue-both") {
      if (!f || !f.kind) { toast("Pick an issue filter first (Issues rail or the Filter dropdown)", "warn"); return; }
      window.location.href = `/api/export/issue?key=${encodeURIComponent(S.filter)}&what=${t === "issue-both" ? "both" : "inlinks"}&format=xlsx`; return;
    }
    window.location.href = `/api/export?key=${encodeURIComponent(S.filter)}&format=${t}&q=${encodeURIComponent(S.q)}${filtersQS()}`;
    if (S.q || colFilterList().length) toast("Exporting only the rows that match your search and column filters");
  });

  async function openExportCentre() {
    openModal("modal-export");
    const box = $("#export-parts");
    if (!box.dataset.loaded) {
      const d = await api("/api/export/parts");
      box.innerHTML = d.parts.map((p) => `<label style="display:flex;gap:8px;align-items:flex-start;padding:5px 0;border-bottom:1px dashed var(--border)"><input type="checkbox" value="${p.key}" ${["overview", "issues", "issue_urls", "issue_inlinks", "matrix"].includes(p.key) ? "checked" : ""}><span><b>${esc(p.name)}</b><small class="fh">${esc(p.desc)}</small></span></label>`).join("");
      box.dataset.loaded = "1";
      box.onchange = updateExportEstimate;
    }
    updateExportEstimate();
  }
  function exportParts() { return $$("#export-parts input:checked").map((i) => i.value); }
  function updateExportEstimate() {
    const parts = exportParts();
    const nIssues = S.tabs.reduce((n, t) => n + t.filters.filter((f) => f.kind && !f.issue_only && f.count).length, 0);
    let sheets = 0;
    if (parts.includes("overview")) sheets += 1; if (parts.includes("issues")) sheets += 1; if (parts.includes("matrix")) sheets += 1;
    if (parts.includes("issue_urls")) sheets += nIssues; if (parts.includes("issue_inlinks")) sheets += nIssues;
    if (parts.includes("tabs")) sheets += S.tabs.filter((t) => (t.filters[0] || {}).count).length;
    if (parts.includes("filters")) sheets += S.tabs.reduce((n, t) => n + t.filters.filter((f) => f.count && f.short !== "all" && !f.issue_only && ($("#ex-allfilters").checked || f.kind)).length, 0);
    if (parts.includes("links")) sheets += 4; if (parts.includes("reports")) sheets += 12; if (parts.includes("fixes")) sheets += 1;
    $("#export-estimate").textContent = parts.length ? `About ${fmtNum(sheets)} sheet${sheets === 1 ? "" : "s"} · ${nIssues} issue types triggered in this crawl` : "Nothing selected";
    $("#ex-download").disabled = !parts.length;
  }
  $("#ex-allfilters").onchange = updateExportEstimate;
  $("#ex-select-issues").onclick = () => { $$("#export-parts input").forEach((i) => { i.checked = ["overview", "issues", "issue_urls", "issue_inlinks", "matrix"].includes(i.value); }); updateExportEstimate(); };
  $("#ex-select-all").onclick = () => { $$("#export-parts input").forEach((i) => { i.checked = true; }); updateExportEstimate(); };
  $("#ex-download").onclick = () => {
    const fmt = ($("input[name=exfmt]:checked") || {}).value || "xlsx";
    window.location.href = `/api/export/bundle?parts=${encodeURIComponent(exportParts().join(","))}&format=${fmt}&all_filters_too=${$("#ex-allfilters").checked ? 1 : 0}`;
    toast("Building your export — the download starts when it's ready (large crawls take a few seconds)");
  };

  async function loadReports() {
    const d = await api("/api/reports");
    $("#reports-menu").innerHTML = d.reports.map((r) => `<a href="#" data-report="${r.key}">${esc(r.name)}</a>`).join("");
    $$("#reports-menu a").forEach((a) => a.onclick = async (e) => { e.preventDefault(); await showReport(a.dataset.report, a.textContent); });
  }
  async function showReport(key, name) {
    $("#report-title").textContent = name; $("#report-csv").href = `/api/report/${key}?format=csv`; $("#report-xlsx").href = `/api/report/${key}?format=xlsx`;
    $("#report-body").innerHTML = skel.rows(10); openModal("modal-report");
    try {
      const d = await api(`/api/report/${key}?format=json`);
      $("#report-body").innerHTML = d.sheets.map((s) => `<h3>${esc(s.name)} <span class="muted">(${fmtNum(s.rows.length)} rows${s.rows.length >= 2000 ? ", first 2000 shown" : ""})</span></h3><div style="overflow:auto;max-height:65vh"><table class="report-table"><tr>${s.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>${s.rows.map((r) => `<tr>${r.map((v) => `<td title="${esc(v)}">${esc(v)}</td>`).join("")}</tr>`).join("")}</table></div>`).join("");
    } catch (e) { $("#report-body").innerHTML = empty("!", "Report unavailable", e.message); }
  }

  // ------------------------------------------------------------------ modals
  function openModal(id) { $(`#${id}`).classList.remove("hidden"); }
  function closeModal(id) { $(`#${id}`).classList.add("hidden"); }
  $$("[data-close]").forEach((b) => b.onclick = () => closeModal(b.dataset.close));
  $$(".modal").forEach((m) => m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); });

  // ------------------------------------------------------------------ config
  const CFG_GROUPS = [
    ["Crawl", [["crawl_images", "Crawl images", "bool"], ["crawl_css", "Crawl CSS", "bool"], ["crawl_js", "Crawl JavaScript", "bool"], ["crawl_media", "Crawl media", "bool"],
      ["check_external", "Check external links", "bool"], ["crawl_canonicals", "Crawl canonicals", "bool"], ["crawl_pagination", "Crawl pagination (next/prev)", "bool"],
      ["crawl_hreflang", "Crawl hreflang", "bool"], ["crawl_amp", "Crawl AMP", "bool"], ["crawl_meta_refresh", "Crawl meta refresh", "bool"], ["crawl_iframes", "Crawl iframes", "bool"],
      ["crawl_subdomains", "Crawl all subdomains", "bool"], ["crawl_outside_start_folder", "Crawl outside of start folder", "bool"],
      ["follow_internal_nofollow", "Follow internal nofollow", "bool"], ["follow_external_nofollow", "Follow external nofollow", "bool"],
      ["crawl_sitemaps", "Crawl linked XML sitemaps (robots.txt)", "bool"], ["skip_share_links", "Skip social share-intent links (facebook/twitter/linkedin share URLs)", "bool"], ["extract_structured_data", "Extract structured data (JSON-LD, Microdata, RDFa)", "bool"],
      ["validate_schema_org", "Validate against schema.org", "bool"], ["validate_google", "Validate Google rich-result requirements", "bool"],
      ["sitemap_urls", "Extra sitemap URLs (one per line)", "lines"]]],
    ["Limits", [["max_urls", "Limit crawl total (0 = unlimited)", "int"], ["max_depth", "Limit crawl depth (0 = unlimited)", "int"], ["max_redirects", "Max redirects to follow", "int"],
      ["max_url_length", "Max URL length", "int"], ["max_page_size_kb", "Max page size (KB)", "int"], ["max_query_strings", "Limit query strings (0 = unlimited)", "int"]]],
    ["Rendering", [["rendering", "Rendering mode", "select:text=Text Only,javascript=JavaScript (headless Chrome)"], ["render_ajax_timeout", "AJAX timeout (s) - wait for network idle after load", "num"],
      ["render_wait_extra", "Extra wait after idle (s)", "num"], ["render_width", "Window width", "int"], ["render_height", "Window height", "int"], ["render_mobile", "Emulate mobile (touch + mobile viewport)", "bool"],
      ["render_screenshots", "Store rendered page screenshots", "bool"], ["render_full_page_screenshot", "Full-page screenshots (slower, bigger files)", "bool"],
      ["render_block_images", "Block images/fonts/media in the browser (faster)", "bool"], ["render_concurrency", "Concurrent browser pages", "int"]]],
    ["Advanced", [["respect_robots", "Respect robots.txt", "bool"], ["robots_report_only", "Ignore robots.txt but report status", "bool"], ["respect_noindex", "Respect noindex (do not follow links from noindex pages)", "bool"],
      ["store_html", "Store HTML", "bool"], ["extract_srcset", "Extract images from srcset", "bool"], ["ignore_non_indexable_for_issues", "Ignore non-indexable URLs for issues", "bool"],
      ["ignore_paginated_for_duplicates", "Ignore paginated URLs for duplicate filters", "bool"], ["timeout", "Response timeout (s)", "num"], ["retries_5xx", "5xx response retries", "int"], ["verify_ssl", "Verify SSL certificates", "bool"]]],
    ["Speed & requests", [["max_threads", "Max threads", "int"], ["max_urls_per_second", "Max URLs/s (0 = unlimited)", "num"], ["user_agent", "User-Agent", "ua"],
      ["custom_headers", "Custom headers (Name: value per line)", "headers"], ["auth_user", "Basic auth user", "text"], ["auth_pass", "Basic auth password", "text"]]],
    ["Include / Exclude / Rewrite", [["include_regex", "Include (regex, one per line)", "lines"], ["exclude_regex", "Exclude (regex, one per line)", "lines"],
      ["remove_params", "Remove parameters (one per line)", "lines"], ["cdn_domains", "CDN domains treated as internal (one per line)", "lines"]]],
    ["Preferences", [["title_max_chars", "Title max characters", "int"], ["title_min_chars", "Title min characters", "int"], ["title_max_px", "Title max pixels", "int"], ["title_min_px", "Title min pixels", "int"],
      ["desc_max_chars", "Description max characters", "int"], ["desc_min_chars", "Description min characters", "int"], ["desc_max_px", "Description max pixels", "int"], ["desc_min_px", "Description min pixels", "int"],
      ["url_max_chars", "URL max characters", "int"], ["h1_max_chars", "H1 max characters", "int"], ["h2_max_chars", "H2 max characters", "int"], ["alt_max_chars", "Alt text max characters", "int"],
      ["image_max_kb", "Image max size (KB)", "int"], ["low_content_words", "Low content word count", "int"], ["high_crawl_depth", "High crawl depth", "int"],
      ["high_internal_outlinks", "High internal outlinks", "int"], ["high_external_outlinks", "High external outlinks", "int"], ["near_duplicate_threshold", "Near duplicate similarity (%)", "int"],
      ["non_descriptive_anchors", "Non-descriptive anchor text (one per line)", "lines"]]],
    ["Spelling & grammar", [["spell_check", "Check spelling (dictionary)", "bool"], ["grammar_check", "Check grammar", "bool"],
      ["spell_language", "Fallback language (when the page has no lang attribute)", "select:en=English,de=German,fr=French,es=Spanish,pt=Portuguese,it=Italian,nl=Dutch,ru=Russian"],
      ["spell_learn_min_pages", "Learn site vocabulary: unknown word on at least N pages", "int"], ["spell_learn_share", "… and on at least this % of pages", "int"],
      ["grammar_lt_url", "LanguageTool server URL (optional)", "text"], ["spell_ignore", "Dictionary: words never flagged (one per line)", "lines"], ["_spell_run", "", "spellrun"]]],
    ["Search Console & GA4", [["google_client_id", "OAuth client ID", "text"], ["google_client_secret", "OAuth client secret", "password"], ["_google_connect", "", "gconnect"],
      ["gsc_property", "Search Console property", "gprop"], ["ga4_property", "GA4 property", "gprop"], ["gsc_days", "Date range (days, ending 3 days ago)", "int"],
      ["gsc_auto", "Fetch automatically after each crawl", "bool"], ["gsc_inspect_max", "URL Inspection: pages per run (0 = off)", "int"]]],
    ["Link metrics", [["ahrefs_token", "Ahrefs API token", "password"], ["ahrefs_country", "Ahrefs country (keywords / traffic)", "text"], ["moz_access_id", "Moz access ID", "text"], ["moz_secret", "Moz secret key", "password"],
      ["majestic_key", "Majestic API key", "password"], ["majestic_datasource", "Majestic index", "select:fresh=Fresh,historic=Historic"], ["linkmetrics_max_urls", "URLs per run (credit cap)", "int"], ["linkmetrics_auto", "Fetch automatically after each crawl (top pages)", "bool"]]],
    ["PageSpeed", [["lighthouse_enabled", "Run Lighthouse automatically after each crawl", "bool"], ["lighthouse_max_pages", "Pages per automatic run (start page + most linked)", "int"],
      ["lighthouse_device", "Device", "select:mobile=Mobile (Moto G, slow 4G),desktop=Desktop"], ["lighthouse_concurrency", "Parallel Chrome instances (local runs)", "int"],
      ["psi_api_key", "PageSpeed Insights API key (optional)", "password"]]],
    ["AI", [["ai_provider", "Provider", "provider"], ["_ai_help", "", "aihelp"], ["ai_api_key", "API key", "password"], ["ai_model", "Model", "text"],
      ["ai_base_url", "Base URL", "text"], ["ai_concurrency", "Concurrent requests (lower if you hit rate limits)", "int"], ["ai_batch_size", "Pages per request", "int"],
      ["ai_brand", "Brand name (kept in titles)", "text"], ["ai_instructions", "House style / extra instructions for every prompt", "textarea"],
      ["_ai_sysprompt", "Advanced: system prompt for this provider", "sysprompt"], ["_ai_test", "", "aitest"]]],
    ["Connectors", [["targets", "Push targets", "targets"]]],
    ["Email", [["email_enabled", "Email me when a crawl finishes", "bool"], ["email_to", "Send to (comma-separated)", "text"], ["email_from", "From address", "text"],
      ["email_include_compare", "Include what changed since the previous crawl of the site", "bool"],
      ["smtp_host", "SMTP server", "text"], ["smtp_port", "Port", "int"], ["smtp_security", "Security", "select:starttls=STARTTLS (port 587),ssl=SSL/TLS (port 465),none=None (local relay)"],
      ["smtp_user", "SMTP username", "text"], ["smtp_pass", "SMTP password / app password", "password"], ["_email_test", "", "emailtest"]]],
    ["MCP", [["mcp_enabled", "Serve the MCP tools over HTTP while the app runs", "bool"], ["app_port", "App port (applies at next launch)", "int"], ["_mcp_info", "", "mcpinfo"]]],
    ["Custom search", [["custom_search", "Searches — one per line as  name | text  (prefix with ! for 'does not contain', regex: suffix /r)", "search"]]],
    ["Custom extraction", [["custom_extraction", "Extractors — one per line as  name | xpath|css|regex | expression", "extract"]]],
  ];

  const CFG_HINTS = {
    "Crawl": "What the spider follows and fetches. Untick resource types you don't need to make crawls faster.",
    "Rendering": "Text Only parses the HTML the server sends. JavaScript renders each page in headless Chrome (slower) and enables the JavaScript tab, screenshots and console log.",
    "Limits": "Stop the crawl early. 0 means no limit.",
    "Advanced": "Robots handling, what to store, and how issues are scoped.",
    "Speed & requests": "Threads, rate limiting and how the spider identifies itself. Lower threads if a site starts returning 429/503.",
    "Include / Exclude / Rewrite": "Regex patterns are matched against the full URL. Include = crawl only matching URLs; Exclude = never crawl matching URLs.",
    "Preferences": "Thresholds used by the Page Titles, Meta Description, H1/H2, Images, Content and Links filters.",
    "Spelling & grammar": "Runs at the end of a crawl (and on Analyse) over the stored page text. Off by default, like Screaming Frog. Spelling uses an offline dictionary and learns your site's own words; grammar uses built-in rules or a LanguageTool server.",
    "Search Console & GA4": "Clicks, impressions, CTR and position per page from Search Console; sessions, engagement and key events from GA4; optional index status from the URL Inspection API. Sign in with your own Google Cloud OAuth client (Desktop app type) — nothing goes through a third party.",
    "Link metrics": "Per-page authority and backlink numbers from Ahrefs, Moz and Majestic in the Link Metrics tab. Only providers with a key are queried, and every run is capped because each URL costs API credits.",
    "PageSpeed": "Lighthouse audits: performance (Core Web Vitals), accessibility (axe-core), best practices and SEO. Runs locally in the bundled headless Chromium (~25 s per page, no key). Add a PageSpeed Insights key to use Google's servers instead and get real-user CrUX field data.",
    "AI": "Bring your own key. Fix proposals are written by this provider; keys are stored only in settings.json on this machine.",
    "Connectors": "Where approved fixes get pushed. Add a staging and a live target per site; pushes to LIVE always ask you to type the target name.",
    "Email": "A crawl-complete report in your inbox: totals, what changed since the last crawl of the site, and every triggered issue with its priority and URL count.",
    "MCP": "Let Claude Desktop, Claude Code, Cursor or any MCP client drive the spider: start crawls, read issues, query filters, export, review and push fixes. Local only; nothing leaves this PC.",
    "Custom search": "Flag pages whose HTML (or text) contains / does not contain a string. One search per line.",
    "Custom extraction": "Pull values out of every page with XPath, CSS selectors or regex. One extractor per line.",
  };
  let cfgTab = "Crawl";
  const FIELD_HELP = {
    crawl_images: "Fetches every image to report size, alt text and broken images. Off = faster crawl, Images tab empty.",
    crawl_css: "Fetches stylesheets for status codes and size.", crawl_js: "Fetches script files for status codes and size.",
    crawl_media: "Video/audio files.", check_external: "Requests every external link once to catch broken outbound links. Off = external URLs listed but not fetched.",
    crawl_canonicals: "Follows canonical URLs so canonical targets get crawled.", crawl_pagination: "Follows rel=next / rel=prev.",
    crawl_hreflang: "Follows hreflang alternates so return links can be checked.", crawl_amp: "Follows amphtml links.", crawl_meta_refresh: "Follows meta-refresh redirects.",
    crawl_iframes: "Fetches iframe sources.", crawl_subdomains: "Treat blog.example.com, shop.example.com… as part of the site. Off = other subdomains count as external.",
    crawl_outside_start_folder: "Starting at /blog/ with this off keeps the crawl inside /blog/.", follow_internal_nofollow: "Crawl internal links marked rel=nofollow.",
    follow_external_nofollow: "Check external links marked rel=nofollow.", crawl_sitemaps: "Reads Sitemap: lines in robots.txt and /sitemap.xml so orphan pages are found.",
    extract_structured_data: "Fills the Structured Data tab and pane. A few milliseconds per page.", validate_schema_org: "Flags unknown types and properties, and properties not expected on a type (bundled vocabulary, offline).",
    validate_google: "Required properties missing = error, recommended missing = warning, per Google's rich-result documentation (Article, Product, Breadcrumb, HowTo, Event, LocalBusiness, Video, Job, Recipe, Review…). FAQ is not checked (retired rich result).",
    skip_share_links: "facebook.com/sharer, twitter.com/intent, linkedin share links: recorded but never fetched (they are slow and tell you nothing).",
    sitemap_urls: "Extra sitemap URLs to read in addition to what robots.txt lists.",
    max_urls: "Stops after this many URLs have been fetched (pages + images + scripts + external). Good for a quick look at a big site.",
    max_depth: "Clicks from the start page. 1 = start page and what it links to.", max_redirects: "Hops followed before a redirect is reported as a chain.",
    max_url_length: "URLs longer than this are not crawled.", max_page_size_kb: "Larger responses are cut off (and flagged).", max_query_strings: "URLs with more parameters than this are skipped (crawl-trap protection).",
    rendering: "Text Only reads the HTML as the server sends it (fast). JavaScript opens each page in headless Chrome (5–10× slower) — needed for React/Vue/Angular sites and enables the JavaScript tab.",
    render_ajax_timeout: "After the page loads, wait up to this long for background requests to settle.", render_wait_extra: "Fixed extra wait for pages that keep animating/polling.",
    render_width: "Browser window width in pixels.", render_height: "Browser window height in pixels.", render_mobile: "Mobile viewport + touch, like Googlebot smartphone.",
    render_screenshots: "Keeps a screenshot per page (Rendered Page pane). Adds ~50–150 KB per page to the crawl file.", render_full_page_screenshot: "Capture the whole page instead of the first screen.",
    render_block_images: "Skip image/font downloads inside the browser — faster, but screenshots show blank images.", render_concurrency: "Pages rendered at the same time. 2–4 is sensible on a laptop.",
    respect_robots: "Obey Disallow rules like Google does. Turn off only on your own staging sites.", robots_report_only: "Crawl everything but still report what robots.txt would block.",
    respect_noindex: "Don't follow links from noindex pages (mimics how Google treats them long-term).", store_html: "Keep page source for View Source and AI fixes. Off = smaller crawl files.",
    extract_srcset: "Also fetch responsive image variants (srcset). Off = far fewer image URLs on WordPress sites.",
    ignore_non_indexable_for_issues: "Don't raise title/meta/H1 issues on pages that are noindex, canonicalised or redirected.", ignore_paginated_for_duplicates: "Page 2, 3… of a series won't count as duplicate titles.",
    timeout: "Seconds to wait for a response before reporting No Response.", retries_5xx: "Retry server errors this many times before reporting them.",
    verify_ssl: "Turn off only for staging sites with self-signed certificates.",
    max_threads: "Parallel requests. 5 is polite; above 10 can get you rate-limited (429) or blocked.", max_urls_per_second: "Hard rate limit on top of threads. 0 = no limit.",
    user_agent: "How the crawler identifies itself. Googlebot presets show what Google sees; some sites block unknown bots.",
    custom_headers: "Sent with every request, e.g. a staging password header or Accept-Language.", auth_user: "HTTP basic auth (the browser login popup), not a CMS login.", auth_pass: "",
    include_regex: "Only URLs matching one of these patterns are crawled (the start URL always is). Example:  /blog/", exclude_regex: "URLs matching any pattern are never crawled. Example:  \\?replytocom=",
    remove_params: "Strip these query parameters so ?utm_source=… variants merge into one URL.", cdn_domains: "Domains to treat as internal (images on cdn.example.com).",
    title_max_chars: "Titles above this are flagged.", title_min_chars: "Titles below this are flagged.", title_max_px: "Google truncates titles around 561px on desktop.", title_min_px: "",
    desc_max_chars: "", desc_min_chars: "", desc_max_px: "Google truncates descriptions around 985px.", desc_min_px: "",
    url_max_chars: "", h1_max_chars: "", h2_max_chars: "", alt_max_chars: "", image_max_kb: "Images above this are flagged as large.",
    low_content_words: "Pages with fewer words are flagged as thin.", high_crawl_depth: "Pages deeper than this many clicks are flagged.", high_internal_outlinks: "", high_external_outlinks: "",
    non_descriptive_anchors: "Anchor texts that get flagged, e.g. click here, read more.",
    google_client_id: "Google Cloud Console → APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app. Enable the Search Console API, Google Analytics Data API and Google Analytics Admin API in the same project.",
    google_client_secret: "Shown once when the client is created; download the JSON if unsure.", gsc_property: "Leave on Auto to match the crawled host (a Domain property is preferred).", ga4_property: "",
    gsc_days: "Search Console data is final after ~3 days, so the range ends 3 days ago. 28 is Google's default view; 90 for more stable numbers.",
    gsc_auto: "Runs in the background when a crawl finishes.", gsc_inspect_max: "Each page costs one API call; Google allows 2,000 per property per day. Pages with the most impressions are inspected first.",
    ahrefs_token: "Ahrefs API v3 token (ahrefs.com → Account → API). Returns URL Rating, Domain Rating, live backlinks and referring domains, organic keywords and traffic (4 calls per URL).",
    ahrefs_country: "Two-letter code, e.g. gb, us.", moz_access_id: "Moz Links API v2 (moz.com → Moz API). Page Authority, Domain Authority, Spam Score, linking root domains; 50 URLs per call.", moz_secret: "",
    majestic_key: "Majestic → OpenApps API key. Trust Flow, Citation Flow, external backlinks, referring domains; 100 URLs per call.", majestic_datasource: "Fresh = last ~120 days; Historic = full index (more credits).",
    linkmetrics_max_urls: "The most-linked indexable pages are looked up first.", linkmetrics_auto: "",
    lighthouse_enabled: "Audits the top pages when a crawl finishes (in the background). You can always run it by hand from PageSpeed.", lighthouse_max_pages: "20 pages ≈ 4-8 minutes locally at 2 in parallel.",
    lighthouse_device: "Mobile is what Google scores. Desktop is unthrottled.", lighthouse_concurrency: "Each instance is a full Chrome; 2 is sensible on a laptop, 4 on a desktop.",
    psi_api_key: "Free key from console.cloud.google.com (enable the PageSpeed Insights API). Quota ~25,000 requests/day. With a key, local Chrome is not used.",
    spell_check: "Flags words the dictionary does not know. Capitalised words (names) and acronyms are skipped.",
    grammar_check: "Built-in rules: repeated words, a/an, lowercase sentence starts, punctuation spacing, 'should of', 'alot'… Set a LanguageTool URL below for full grammar checking.",
    spell_language: "Used when a page has no <html lang> or an unsupported one.",
    spell_learn_min_pages: "Brand and product names appear on many pages; words meeting both limits are treated as vocabulary, not typos.", spell_learn_share: "",
    grammar_lt_url: "Self-hosted LanguageTool (docker: erikvl87/languagetool, port 8010) or https://api.languagetool.org (public, rate limited, 20k chars/page). With a server set, it checks spelling too.",
    spell_ignore: "Words added with 'Add to dictionary' in the Spelling & Grammar pane land here.",
    near_duplicate_threshold: "Two pages count as near duplicates when their text is at least this similar (MinHash Jaccard estimate). Screaming Frog's default is 90. Press Analyse after changing it.",
    custom_search: "Example:  Has GTM | GTM-   (flags pages whose HTML contains GTM-).  !Has GTM | GTM-  flags pages that do NOT contain it.",
    custom_extraction: "Examples:  Author | css | .author-name     Price | xpath | //span[@class='price']     SKU | regex | SKU-(\\d+)",
    ai_model: "Leave blank to use the provider's default shown in the placeholder.", ai_concurrency: "", ai_batch_size: "More pages per request = cheaper, slightly less careful.",
    ai_brand: "Added to prompts so titles keep the brand consistently.", ai_instructions: "Tone of voice, words to avoid, title format rules… applied to every proposal.",
    _ai_sysprompt: "Replaces the default persona (\"You are a senior technical SEO editor.\") for this provider only. The task, brand, house style and JSON output rules are always appended. Leave blank for the default.",
    email_to: "Several addresses separated by commas.", email_from: "Leave blank to use the SMTP username. Some providers require it to match the login.",
    email_include_compare: "Adds the Compare summary (issues up/down, pages added/removed/changed) when an earlier completed crawl of the same site exists.",
    smtp_host: "e.g. smtp.gmail.com, smtp.office365.com, smtp.sendgrid.net or your own relay.", smtp_port: "587 for STARTTLS, 465 for SSL/TLS, 25 for an unencrypted local relay.",
    smtp_security: "", smtp_user: "Usually the full email address.", smtp_pass: "Gmail and Outlook need an app password, not your account password.",
    mcp_enabled: "Exposes the tool set at http://127.0.0.1:<port>/mcp (Streamable HTTP) for as long as the app is open. Off = the endpoint answers 404; the stdio launcher still works.",
    app_port: "Fixed port so MCP clients always find the app. If it is busy at launch a random free port is used instead (shown below).",
  };
  const FIELD_GROUP = {};
  CFG_GROUPS.forEach(([g, fields]) => fields.forEach(([k]) => { FIELD_GROUP[k] = g; }));

  function renderConfig(cfg) {
    const body = $("#config-body");
    body.innerHTML = `<nav class="cfg-nav">${CFG_GROUPS.map(([g]) => `<button data-g="${esc(g)}" class="${g === cfgTab ? "active" : ""}">${esc(g)}</button>`).join("")}</nav><div class="cfg-panels">` +
      CFG_GROUPS.map(([g, fields]) => `<div class="cfg-group ${g === cfgTab ? "" : "hidden"}" data-g="${esc(g)}"><h3>${esc(g)}</h3><p class="muted cfg-hint">${esc(CFG_HINTS[g] || "")}</p><div class="cfg-grid">${fields.map(([k, label, type]) => {
      const v = cfg[k];
      if (type === "bool") return `<label><input type="checkbox" data-k="${k}" ${v ? "checked" : ""}><span>${esc(label)}</span></label>`;
      if (type === "int" || type === "num") return `<label><span>${esc(label)}</span><input type="number" step="${type === "num" ? "0.1" : "1"}" data-k="${k}" value="${v ?? 0}"></label>`;
      if (k === "ai_model") return `<label><span>${esc(label)}</span><span class="pw"><input type="text" data-k="${k}" value="${esc(v ?? "")}" list="ai-models" autocomplete="off" spellcheck="false"><datalist id="ai-models"></datalist><button type="button" id="ai-models-refresh" title="Fetch the provider's model list" aria-label="Refresh model list">${ic("refresh-cw")}</button></span><small class="fh" id="ai-model-status"></small></label>`;
      if (type === "text") return `<label><span>${esc(label)}</span><input type="text" data-k="${k}" value="${esc(v ?? "")}"></label>`;
      if (type === "password") return `<label><span>${esc(label)}</span><span class="pw"><input type="password" data-k="${k}" value="${esc(v ?? "")}" autocomplete="off" spellcheck="false"><button type="button" data-pw>Show</button></span></label>`;
      if (type === "aihelp") return `<div class="ai-help" id="ai-help"></div>`;
      if (type === "sysprompt") return `<label class="full"><span>${esc(label)}</span><textarea rows="3" data-k="_ai_sysprompt" data-type="sysprompt" data-prov="${esc(cfg.ai_provider || "anthropic")}" placeholder="You are a senior technical SEO editor." spellcheck="false">${esc(((cfg.ai_system_prompts || {})[cfg.ai_provider] || ""))}</textarea></label>`;
      if (type === "textarea") return `<label class="full"><span>${esc(label)}</span><textarea rows="3" data-k="${k}" data-type="string">${esc(v ?? "")}</textarea></label>`;
      if (type === "provider") return `<label><span>${esc(label)}</span><select data-k="${k}" id="cfg-provider">${(S.aiCatalog.providers || []).map((p) => `<option value="${p.id}" ${p.id === v ? "selected" : ""}>${esc(p.name)}${p.default_model ? ` — default ${p.default_model}` : ""}</option>`).join("")}</select></label>`;
      if (type === "mcpinfo") return `<div class="mcp-info" id="mcp-info" style="grid-column:1/-1">${skel.kv(4)}</div>`;
      if (type === "gconnect") return `<div class="cfg-inline" style="grid-column:1/-1"><button type="button" id="cfg-g-connect">Connect Google account</button><button type="button" id="cfg-g-disconnect" class="hidden">Disconnect</button><span class="muted" id="cfg-g-out"></span></div>`;
      if (type === "gprop") return `<label><span>${esc(label)}</span><select data-k="${k}" data-g-prop="${k}"><option value="${esc(v || "")}">${esc(v || (k === "gsc_property" ? "Auto (match the crawled site)" : "— not set —"))}</option></select></label>`;
      if (type === "spellrun") return `<div class="cfg-inline" style="grid-column:1/-1"><button type="button" id="cfg-spell-run">Run check now</button><span class="muted" id="cfg-spell-out">Saves the settings, then re-checks the open crawl (no re-crawl needed).</span></div>`;
      if (type === "emailtest") return `<div class="cfg-inline" style="grid-column:1/-1"><button type="button" id="cfg-email-test">Send test email</button><span class="muted" id="cfg-email-test-out">Saves the settings first, then emails the report for the open crawl.</span></div>`;
      if (type === "aitest") return `<div class="cfg-inline" style="grid-column:1/-1"><button type="button" id="cfg-ai-test">Test connection</button><span class="muted" id="cfg-ai-test-out">Saves the settings first, then sends a one-word prompt.</span></div>`;
      if (type === "targets") return `<div style="grid-column:1/-1"><table class="targets" id="targets-table"><thead><tr><th>Name</th><th>Type</th><th>Env</th><th>Site / endpoint URL</th><th>Username</th><th>Password / token</th><th></th></tr></thead><tbody>${(v || []).map((t, i) => targetRow(t, i)).join("")}</tbody></table>
        <div class="cfg-inline"><button type="button" id="target-add">+ Add target</button><span class="muted" id="target-test-out"></span></div>
        <p class="cfg-note">${(S.aiCatalog.connectors || []).map((c) => `<b>${esc(c.label)}</b>: ${esc(c.doc.split("\n")[0])}`).join("<br>")}</p></div>`;
      if (type.startsWith("select:")) return `<label><span>${esc(label)}</span><select data-k="${k}">${type.slice(7).split(",").map((o) => { const [val, name] = o.split("="); return `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(name)}</option>`; }).join("")}</select></label>`;
      if (type === "ua") return `<label><span>${esc(label)}</span><select data-k="${k}">${Object.entries(S.userAgents).map(([n, ua]) => `<option value="${esc(ua)}" ${ua === v ? "selected" : ""}>${esc(n)}</option>`).join("")}${Object.values(S.userAgents).includes(v) ? "" : `<option value="${esc(v)}" selected>Custom</option>`}</select></label>`;
      if (type === "lines") return `<label style="grid-column:1/-1;flex-direction:column;align-items:stretch"><span>${esc(label)}</span><textarea rows="3" data-k="${k}" data-type="lines">${esc((v || []).join("\n"))}</textarea></label>`;
      if (type === "headers") return `<label style="grid-column:1/-1;flex-direction:column;align-items:stretch"><span>${esc(label)}</span><textarea rows="3" data-k="${k}" data-type="headers">${esc(Object.entries(v || {}).map(([a, b]) => `${a}: ${b}`).join("\n"))}</textarea></label>`;
      if (type === "search") return `<label style="grid-column:1/-1;flex-direction:column;align-items:stretch"><span>${esc(label)}</span><textarea rows="4" data-k="${k}" data-type="search">${esc((v || []).map((s) => `${s.contains === false ? "!" : ""}${s.name} | ${s.text}${s.regex ? " /r" : ""}`).join("\n"))}</textarea></label>`;
      if (type === "extract") return `<label style="grid-column:1/-1;flex-direction:column;align-items:stretch"><span>${esc(label)}</span><textarea rows="4" data-k="${k}" data-type="extract">${esc((v || []).map((s) => `${s.name} | ${s.type} | ${s.expr}`).join("\n"))}</textarea></label>`;
      return "";
    }).join("")}</div></div>`).join("") + `</div>`;
    $$(".cfg-nav button", body).forEach((b) => b.onclick = () => {
      cfgTab = b.dataset.g;
      $$(".cfg-nav button", body).forEach((x) => x.classList.toggle("active", x === b));
      $$(".cfg-group", body).forEach((p) => p.classList.toggle("hidden", p.dataset.g !== cfgTab));
    });
    $$("[data-pw]", body).forEach((b) => b.onclick = () => { const i = b.previousElementSibling; i.type = i.type === "password" ? "text" : "password"; b.textContent = i.type === "password" ? "Show" : "Hide"; });
    // help text under every setting that has one
    $$("[data-k]", body).forEach((el) => {
      const h = FIELD_HELP[el.dataset.k]; const lab = el.closest("label"); const sp = lab && lab.querySelector("span:first-child");
      if (h && sp && !sp.querySelector(".fh")) sp.insertAdjacentHTML("beforeend", `<small class="fh">${esc(h)}</small>`);
    });
    // rendering: tell the person whether headless Chrome is actually available
    const rsel = $('[data-k="rendering"]', body);
    if (rsel) {
      const note = document.createElement("div"); note.className = "cfg-warn hidden"; rsel.closest("label").insertAdjacentElement("afterend", note);
      const upd = () => {
        if (rsel.value !== "javascript") { note.classList.add("hidden"); return; }
        note.classList.remove("hidden");
        const sc = S.sysCheck || {};
        note.className = "cfg-warn " + (sc.chromium ? "ok" : "");
        note.innerHTML = sc.chromium ? `${ic("check")}Headless Chromium is installed. Expect roughly 1–3 seconds per page; crawls run 5–10× slower than Text Only.`
          : `${ic("x")}Chromium is not installed, so JavaScript crawls cannot start. In a terminal run:<br><code>cd "${esc(S.sysCheck?.app_dir || "the app folder")}" && .venv\\Scripts\\python.exe -m playwright install chromium</code>`;
      };
      rsel.onchange = upd; upd();
    }
    cfgLoaded = JSON.stringify(readConfig());
    body.oninput = body.onchange = () => { $("#config-dirty").classList.toggle("hidden", JSON.stringify(readConfig()) === cfgLoaded); };
    wireAI(body);
    wireMCP(body);
    const aiTest = $("#cfg-ai-test", body);
    if (aiTest) aiTest.onclick = () => busy(aiTest, async () => {
      const out = $("#cfg-ai-test-out", body);
      out.className = "muted"; out.innerHTML = `<span class="spinner"></span> Saving settings and sending a test prompt…`;
      try { await post("/api/config", readConfig()); const r = await post("/api/ai/test"); out.className = "field-ok"; out.innerHTML = ic("check") + "Connected: " + esc(r.message); }
      catch (e) { out.className = "field-err"; out.innerHTML = ic("x") + esc(e.message); }
    });
    wireGoogle(body);
    const spellRun = $("#cfg-spell-run", body);
    if (spellRun) spellRun.onclick = () => busy(spellRun, async () => {
      const out = $("#cfg-spell-out", body);
      out.className = "muted"; out.innerHTML = `<span class="spinner"></span> Saving and checking…`;
      try {
        const r = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readConfig()) });
        if (r.status === 422) { showConfigErrors((await r.json()).errors || {}); out.textContent = "Fix the highlighted fields first."; return; }
        cfgLoaded = JSON.stringify(readConfig());
        const t = await post("/api/spelling/run"); out.className = "field-ok"; out.innerHTML = `${ic("check")}${fmtNum(t.pages)} pages checked: ${fmtNum(t.spelling)} spelling, ${fmtNum(t.grammar)} grammar findings`;
        await loadTabs(); refreshTableSoft();
      } catch (e) { out.className = "field-err"; out.innerHTML = ic("x") + esc(e.message); }
    });
    const emailTest = $("#cfg-email-test", body);
    if (emailTest) emailTest.onclick = () => busy(emailTest, async () => {
      const out = $("#cfg-email-test-out", body);
      out.className = "muted"; out.innerHTML = `<span class="spinner"></span> Saving settings and sending…`;
      try {
        const r = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readConfig()) });
        if (r.status === 422) { showConfigErrors((await r.json()).errors || {}); out.textContent = "Fix the highlighted fields first."; return; }
        const t = await post("/api/email/test"); out.className = "field-ok"; out.innerHTML = ic("check") + esc(t.message);
      } catch (e) { out.className = "field-err"; out.innerHTML = ic("x") + esc(e.message); }
    });
    const add = $("#target-add", body);
    if (add) add.onclick = () => { const tb = $("#targets-table tbody", body); tb.insertAdjacentHTML("beforeend", targetRow({ name: "", type: "wordpress", env: "staging", base_url: "", username: "", secret: "" }, tb.children.length)); wireTargets(body); };
    wireTargets(body);
  }

  async function wireMCP(root) {
    const box = $("#mcp-info", root);
    if (!box) return;
    let info;
    try { info = await api("/api/mcp/info"); } catch (e) { box.innerHTML = `<div class="cfg-warn">${ic("x")}Could not read the MCP status: ${esc(e.message)}</div>`; return; }
    const sn = info.snippets || {};
    const snip = (title, hint, code) => `<div class="mcp-snip"><div class="mcp-snip-head"><b>${esc(title)}</b><span class="muted">${esc(hint)}</span><button type="button" data-copy>Copy</button></div><pre>${esc(code)}</pre></div>`;
    const portNote = info.port && info.configured_port && info.port !== info.configured_port ? ` · configured port ${info.configured_port} was busy, running on ${info.port}` : "";
    if (info.installed && !info.ready) { box.innerHTML = skel.kv(4); setTimeout(() => wireMCP(root), 1200); return; }
    box.innerHTML = `
      <div class="cfg-warn ${info.enabled ? "ok" : ""}">${info.installed ? (info.enabled ? ic("check") : ic("circle")) : ic("x")}${info.installed ? `${info.enabled ? "Serving" : "Endpoint off"} — <code>${esc(info.http_url)}</code> · ${info.tools} tools${portNote}` : "The mcp package is not installed: run  .venv\Scripts\python.exe -m pip install -r requirements.txt"}
        <span class="spacer"></span><a class="btn" href="/api/mcp/tools.md" title="Download the tool list as Markdown">${ic("download")}View tools</a></div>
      <p class="cfg-note">Two ways in. <b>HTTP</b> uses this running app, so tools act on the crawl you see here. <b>stdio</b> launches <code>mcp_server.py</code>, which attaches to this app when it is open and otherwise runs a headless copy.</p>
      ${snip("Claude Code (HTTP)", "run in any terminal", sn.claude_code_http || "")}
      ${snip("Claude Code (stdio)", "alternative when the app is not always open", sn.claude_code_stdio || "")}
      ${snip("Claude Desktop", "Settings → Developer → Edit Config (claude_desktop_config.json)", sn.claude_desktop || "")}
      ${snip("Cursor / other HTTP clients", ".cursor/mcp.json", sn.cursor || "")}`;
    $$("[data-copy]", box).forEach((b) => b.onclick = async () => { try { await navigator.clipboard.writeText(b.closest(".mcp-snip").querySelector("pre").textContent); b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1500); } catch (_) { toast("Clipboard not available — select the text and copy", "warn"); } });
  }

  const AI_HELP = {
    anthropic: { key: true, base: false, help: `Create a key at <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com → API Keys</a> (starts with <code>sk-ant-</code>). Default model <code>claude-opus-5-5</code>; cheaper options <code>claude-sonnet-5-5</code> or <code>claude-haiku-5-5</code>.` },
    openai: { key: true, base: false, help: `Create a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com → API keys</a> (starts with <code>sk-</code>). Default model <code>gpt-4.1</code>; cheaper <code>gpt-4.1-mini</code>.` },
    gemini: { key: true, base: false, help: `Create a key at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener">aistudio.google.com → Get API key</a>. Default model <code>gemini-2.5-flash</code>.` },
    ollama: { key: false, base: true, help: `Runs on this PC, no key and no cost. Install from <a href="https://ollama.com" target="_blank" rel="noopener">ollama.com</a>, then run <code>ollama pull llama3.1</code>. Base URL is normally <code>http://localhost:11434/v1</code>; model is the name you pulled.` },
    custom: { key: true, base: true, help: `Any OpenAI-compatible API (Groq, Mistral, OpenRouter, Together, LM Studio…). Base URL must end in <code>/v1</code>, e.g. <code>https://api.groq.com/openai/v1</code>, plus that service's key and model name.` },
  };
  let modelsTimer = null, modelsSeq = 0;
  async function refreshModels(root) {
    const status = $("#ai-model-status", root), list = $("#ai-models", root), mi = $(`[data-k="ai_model"]`, root);
    if (!status || !list || !mi) return;
    const seq = ++modelsSeq;
    status.className = "fh"; status.innerHTML = `<span class="spinner"></span> Checking the provider's model list…`;
    try {
      const snapshot = JSON.stringify(readConfig());
      await post("/api/config", readConfig());            // the server lists models for the saved provider / key / URL
      if (cfgLoaded === snapshot) cfgLoaded = JSON.stringify(readConfig());
      const d = await api("/api/ai/models");
      if (seq !== modelsSeq) return;
      S.aiModels = d.models || [];
      list.innerHTML = S.aiModels.map((m) => `<option value="${esc(m)}">`).join("");
      const typed = mi.value.trim() || mi.placeholder.replace(" (default)", "");
      if (d.error) { status.className = "fh"; status.textContent = `Could not list models: ${d.error}`; }
      else if (!S.aiModels.length) { status.className = "fh"; status.textContent = "The provider returned no models."; }
      else if (S.aiModels.includes(typed)) { status.className = "fh field-ok"; status.innerHTML = `${ic("check")}'${esc(typed)}' is available (${S.aiModels.length} models listed; click the box to pick another)`; }
      else { status.className = "fh field-err"; status.innerHTML = `${ic("x")}'${esc(typed)}' is not in this provider's list of ${S.aiModels.length} models. Pick one from the dropdown.`; }
    } catch (e) { if (seq === modelsSeq) { status.className = "fh"; status.textContent = `Could not list models: ${e.message}`; } }
  }
  function wireAI(root) {
    const prov = $("#cfg-provider", root);
    if (!prov) return;
    S.sysPrompts = { ...(S.config.ai_system_prompts || {}) };
    let prevProv = prov.value;
    const sp = $("[data-type=sysprompt]", root);
    const row = (k) => { const el = $(`[data-k="${k}"]`, root); return el ? el.closest("label") : null; };
    const update = () => {
      const p = prov.value, meta = AI_HELP[p] || AI_HELP.custom;
      const cat = (S.aiCatalog.providers || []).find((x) => x.id === p) || {};
      $("#ai-help", root).innerHTML = meta.help;
      const keyRow = row("ai_api_key"), baseRow = row("ai_base_url"), modelRow = row("ai_model");
      if (keyRow) { keyRow.classList.toggle("hidden", !meta.key); keyRow.querySelector("span:first-child").classList.toggle("req", meta.key); }
      if (baseRow) { baseRow.classList.toggle("hidden", !meta.base); baseRow.querySelector("span:first-child").classList.toggle("req", meta.base); const bi = baseRow.querySelector("input"); bi.placeholder = p === "ollama" ? "http://localhost:11434/v1" : "https://…/v1"; }
      if (modelRow) { const mi = modelRow.querySelector("input"); mi.placeholder = cat.default_model ? `${cat.default_model} (default)` : "model name"; modelRow.querySelector("span:first-child").classList.toggle("req", !cat.default_model); }
      const test = $("#cfg-ai-test", root), out = $("#cfg-ai-test-out", root);
      const keyVal = ($(`[data-k="ai_api_key"]`, root) || {}).value || "", baseVal = ($(`[data-k="ai_base_url"]`, root) || {}).value || "";
      const missing = (meta.key && !keyVal.trim()) ? "Enter the API key to test" : (meta.base && !baseVal.trim() && p === "custom") ? "Enter the base URL to test" : "";
      if (test) { test.disabled = !!missing; if (out && !out.classList.contains("field-ok") && !out.classList.contains("field-err")) out.textContent = missing || "Saves the settings first, then sends a one-word prompt."; }
    };
    const scheduleModels = () => {
      clearTimeout(modelsTimer);
      const meta = AI_HELP[prov.value] || AI_HELP.custom, keyVal = ($(`[data-k="ai_api_key"]`, root) || {}).value || "", st = $("#ai-model-status", root);
      if (meta.key && !keyVal.trim()) { if (st) { st.className = "fh"; st.textContent = "Enter the API key to check the model name against the provider's list."; } return; }
      modelsTimer = setTimeout(() => refreshModels(root), 700);
    };
    prov.onchange = () => {
      if (sp) { S.sysPrompts[sp.dataset.prov] = sp.value; sp.value = S.sysPrompts[prov.value] || ""; sp.dataset.prov = prov.value; }
      $("#cfg-ai-test-out", root).className = "muted"; $("#ai-models", root).innerHTML = ""; S.aiModels = []; update(); scheduleModels();
    };
    ["ai_api_key", "ai_base_url"].forEach((k) => { const el = $(`[data-k="${k}"]`, root); if (el) el.oninput = () => { update(); scheduleModels(); }; });
    const mi = $(`[data-k="ai_model"]`, root);
    if (mi) mi.oninput = () => {
      const st = $("#ai-model-status", root), typed = mi.value.trim() || mi.placeholder.replace(" (default)", "");
      if (!S.aiModels || !S.aiModels.length || !st) return;
      const ok = S.aiModels.includes(typed);
      st.className = "fh " + (ok ? "field-ok" : "field-err"); st.innerHTML = ok ? `${ic("check")}'${esc(typed)}' is available` : `${ic("x")}'${esc(typed)}' is not in this provider's list of ${S.aiModels.length} models.`;
    };
    const rf = $("#ai-models-refresh", root); if (rf) rf.onclick = () => busy(rf, () => refreshModels(root));
    update(); scheduleModels();
  }

  function targetRow(t, i) {
    const types = (S.aiCatalog.connectors || []).map((c) => `<option value="${c.type}" ${c.type === t.type ? "selected" : ""}>${esc(c.label)}</option>`).join("");
    return `<tr data-i="${i}"><td><input data-f="name" value="${esc(t.name || "")}" placeholder="e.g. Staging"></td><td><select data-f="type">${types}</select></td>
      <td><select data-f="env"><option value="staging" ${t.env !== "live" ? "selected" : ""}>staging</option><option value="live" ${t.env === "live" ? "selected" : ""}>live</option></select></td>
      <td><input data-f="base_url" value="${esc(t.base_url || "")}" placeholder="https://"></td><td><input data-f="username" value="${esc(t.username || "")}"></td>
      <td><input data-f="secret" type="password" value="${esc(t.secret || "")}" autocomplete="off"></td><td class="act"><button type="button" data-test>Test</button><button type="button" data-remove aria-label="Remove">${ic("x")}</button></td></tr>`;
  }
  function readTargets(root) {
    return $$("#targets-table tbody tr", root).map((tr) => Object.fromEntries($$("[data-f]", tr).map((el) => [el.dataset.f, el.value.trim()]))).filter((t) => t.name && t.base_url);
  }
  function wireTargets(root) {
    $$("#targets-table [data-remove]", root).forEach((b) => b.onclick = () => b.closest("tr").remove());
    $$("#targets-table [data-test]", root).forEach((b) => b.onclick = () => busy(b, async () => {
      const tr = b.closest("tr"); const t = Object.fromEntries($$("[data-f]", tr).map((el) => [el.dataset.f, el.value.trim()]));
      const out = $("#target-test-out", root);
      try { const r = await post("/api/targets/test", { target: t }); out.innerHTML = (r.ok ? ic("check") : ic("x")) + esc(r.message + (r.detail ? " · " + r.detail : "")); } catch (e) { out.innerHTML = ic("x") + esc(e.message); }
    }));
  }

  function readConfig() {
    const out = {};
    out.targets = readTargets($("#config-body"));
    const sp = $("#config-body [data-type=sysprompt]"), prov = $("#cfg-provider");
    if (sp && prov) { S.sysPrompts = { ...(S.sysPrompts || {}), [sp.dataset.prov || prov.value]: sp.value }; out.ai_system_prompts = Object.fromEntries(Object.entries(S.sysPrompts).filter(([, v]) => v.trim())); }
    $$("#config-body [data-k]").forEach((el) => {
      const k = el.dataset.k, t = el.dataset.type;
      if (t === "sysprompt") return;
      if (t === "string") { out[k] = el.value; return; }
      if (el.type === "checkbox") out[k] = el.checked;
      else if (el.type === "number") out[k] = Number(el.value || 0);
      else if (t === "lines") out[k] = el.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      else if (t === "headers") out[k] = Object.fromEntries(el.value.split(/\r?\n/).map((s) => s.split(/:(.*)/)).filter((p) => p[0] && p[0].trim()).map((p) => [p[0].trim(), (p[1] || "").trim()]));
      else if (t === "search") out[k] = el.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean).map((line) => { const neg = line.startsWith("!"); if (neg) line = line.slice(1); const rx = / \/r$/.test(line); line = line.replace(/ \/r$/, ""); const [name, ...rest] = line.split("|"); const text = rest.join("|").trim(); return { name: name.trim(), text: text || name.trim(), contains: !neg, regex: rx }; });
      else if (t === "extract") out[k] = el.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean).map((line) => { const p = line.split("|"); return { name: (p[0] || "").trim(), type: (p[1] || "xpath").trim().toLowerCase(), expr: p.slice(2).join("|").trim() }; }).filter((x) => x.expr);
      else out[k] = el.value;
    });
    return out;
  }

  let cfgLoaded = "";
  $("#btn-config").onclick = async (ev, tab) => {
    const [d, cat, sc] = await Promise.all([api("/api/config"), api("/api/ai/catalog"), api("/api/system/check").catch(() => ({}))]);
    S.config = d.config; S.defaults = d.defaults; S.userAgents = d.user_agents; S.aiCatalog = cat; S.sysCheck = sc;
    if (typeof tab === "string") cfgTab = tab;
    renderConfig(S.config); $("#config-dirty").classList.add("hidden"); openModal("modal-config");
  };
  function closeConfig(force) {
    const dirty = JSON.stringify(readConfig()) !== cfgLoaded;
    const c = $("#config-cancel");
    if (dirty && !force && c.dataset.armed !== "1") { c.dataset.armed = "1"; c.textContent = "Discard changes?"; toast("You have unsaved changes. Click again to discard, or Save.", "warn"); setTimeout(() => { c.dataset.armed = ""; c.textContent = "Cancel"; }, 4000); return; }
    c.dataset.armed = ""; c.textContent = "Cancel"; closeModal("modal-config");
  }
  $("#config-cancel").onclick = () => closeConfig(false);
  $("#modal-config .close").onclick = () => closeConfig(false);
  $("#modal-config").onclick = (e) => { if (e.target === $("#modal-config")) closeConfig(false); };
  function showConfigErrors(errors) {
    $$("#config-body .invalid").forEach((el) => el.classList.remove("invalid"));
    $$("#config-body .fh.err").forEach((el) => { el.classList.remove("err"); el.textContent = el.dataset.orig || el.textContent; });
    $$(".cfg-nav .dot").forEach((d) => d.remove());
    const keys = Object.keys(errors);
    keys.forEach((k) => {
      const el = $(`#config-body [data-k="${k}"]`) || (k === "targets" ? $("#targets-table") : null);
      const lab = el && (el.closest("label") || el.closest("div"));
      if (lab) { lab.classList.add("invalid"); let fh = lab.querySelector(".fh"); if (!fh) { fh = document.createElement("small"); fh.className = "fh"; (lab.querySelector("span:first-child") || lab).appendChild(fh); } fh.dataset.orig = fh.dataset.orig || fh.textContent; fh.textContent = errors[k]; fh.classList.add("err"); }
      const g = FIELD_GROUP[k]; const nb = g && $(`.cfg-nav button[data-g="${g}"]`); if (nb && !nb.querySelector(".dot")) nb.insertAdjacentHTML("beforeend", `<span class="dot"></span>`);
    });
    const first = FIELD_GROUP[keys[0]]; if (first) $(`.cfg-nav button[data-g="${first}"]`).click();
    toast(`${keys.length} setting${keys.length > 1 ? "s need" : " needs"} fixing — see the highlighted field${keys.length > 1 ? "s" : ""}`, "error", 6000);
  }

  // ------------------------------------------------------------------ AI fixes
  $$("#fixai-menu a").forEach((a) => a.onclick = async (e) => {
    e.preventDefault();
    const scope = a.dataset.scope;
    const ids = scope === "selected" ? (S.table ? S.table.getSelectedData().map((r) => r.id) : []) : [];
    if (scope === "selected" && !ids.length) { toast("Select rows first (Ctrl/Shift-click)", "warn"); return; }
    const cfg = (await api("/api/config")).config;
    const ruleKinds = new Set(["code", "site", "url", "redirect_link", "image", "canonical"]);
    const needsAI = !ruleKinds.has(f.fixKind || "");
    if (needsAI && !cfg.ai_api_key && !["ollama", "custom"].includes(cfg.ai_provider)) { toast("Add your AI provider key in Config → AI first", "warn"); $("#btn-config").onclick(null, "AI"); return; }
    try {
      const r = await post("/api/fixes/generate", { key: S.filter, ids });
      toast(r.rule ? (r.kind === "site" ? "Building one site-level fix from the crawl data (no AI needed)…" : `Building ${r.kind.replace("_", " ")} fixes from the crawl evidence (no AI needed)…`)
                   : `Generating ${r.kind.replace("_", " ")} fixes with AI${scope === "selected" ? ` for ${ids.length} rows` : " for this filter"}…`);
      openFixes();
    } catch (err) { toast(err.message, "error"); }
  });

  let fxTimer = null;
  async function pollFixJob() {
    try {
      const s = await api("/api/fixes/status");
      const pill = $("#fixes-count");
      const open = (s.counts && (s.counts.proposed || 0) + (s.counts.approved || 0)) || 0;
      pill.textContent = fmtNum(open); pill.classList.toggle("hidden", !open);
      const p = $("#fx-progress");
      if (s.state === "running") {
        p.innerHTML = `<span class="spinner"></span> Generating… ${fmtNum(s.done)} / ${fmtNum(s.total)} URLs · ${fmtNum(s.created)} proposals <a href="#" id="fx-cancel">cancel</a>`;
        const c = $("#fx-cancel"); if (c) c.onclick = (e) => { e.preventDefault(); post("/api/fixes/cancel"); };
        if (!$("#modal-fixes").classList.contains("hidden") && s.done % 8 === 0) loadFixes(true);
      } else if (s.state === "done" || s.state === "cancelled" || s.state === "error") {
        if (fxTimer && S.lastFixState === "running") { toast(s.state === "error" ? `Fix generation failed: ${s.errors.join("; ")}` : `${fmtNum(s.created)} proposals ready${s.errors.length ? ` (${s.errors.length} batches failed)` : ""}`, s.state === "error" ? "error" : "info"); loadFixes(); }
        p.textContent = s.errors.length ? `Last run: ${s.errors[s.errors.length - 1]}` : "";
      } else p.textContent = "";
      S.lastFixState = s.state;
      if (s.state === "running" && !fxTimer) fxTimer = setInterval(pollFixJob, 1500);
      if (s.state !== "running" && fxTimer) { clearInterval(fxTimer); fxTimer = null; }
    } catch (_) {}
  }
  setInterval(() => { if (!fxTimer && (S.lastFixState === "running" || !$("#modal-fixes").classList.contains("hidden") || S.hasCrawl)) pollFixJob(); }, 20000);

  function openFixes() { openModal("modal-fixes"); loadFixes(); pollFixJob(); }
  $("#btn-fixes").onclick = openFixes;
  ["#fx-status", "#fx-kind"].forEach((s) => $(s).onchange = () => loadFixes());
  let fxq; $("#fx-q").oninput = () => { clearTimeout(fxq); fxq = setTimeout(() => loadFixes(), 300); };

  async function loadFixes(soft) {
    const body = $("#fixes-body");
    if (!soft) body.innerHTML = skel.rows(10);
    let d;
    try { d = await api(`/api/fixes?status=${encodeURIComponent($("#fx-status").value)}&kind=${encodeURIComponent($("#fx-kind").value)}&q=${encodeURIComponent($("#fx-q").value.trim())}`); }
    catch (e) { body.innerHTML = empty("spider", "No crawl open", "Open a crawl, pick a filter and use Fix with AI."); return; }
    S.fixRows = d.data;
    const kindSel = $("#fx-kind"); const cur = kindSel.value;
    kindSel.innerHTML = `<option value="">All kinds</option>` + d.kinds.map((k) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(k.replace("_", " "))}</option>`).join("");
    const cfg = (await api("/api/config")).config;
    const ts = $("#fx-target"); ts.innerHTML = (cfg.targets || []).length ? cfg.targets.map((t) => `<option value="${esc(t.name)}" data-env="${esc(t.env)}">${esc(t.name)} (${esc(t.env)} · ${esc(t.type)})</option>`).join("") : `<option value="">No targets — add one in Config → Connectors</option>`;
    const nApproved = d.data.filter((f) => f.status === "approved").length;
    const pushBtn = $("#fx-push");
    pushBtn.textContent = nApproved ? `Push ${fmtNum(nApproved)} approved` : "Push";
    pushBtn.disabled = !nApproved || !(cfg.targets || []).length;
    pushBtn.title = !(cfg.targets || []).length ? "Add a push target in Config → Connectors first" : !nApproved ? "Approve at least one fix to push" : `Send ${nApproved} approved fix${nApproved > 1 ? "es" : ""} to the selected target`;
    ts.onchange = () => { const live = (ts.selectedOptions[0] || {}).dataset?.env === "live"; $("#fx-confirm").classList.toggle("hidden", !live); if (live) toast("This target is LIVE — type its name in the box before pushing", "warn"); }; ts.onchange();
    if (!d.data.length) { body.innerHTML = empty("sparkles", d.total === 0 && !$("#fx-status").value && !$("#fx-kind").value ? "No fixes yet" : "Nothing matches", "Pick a filter in the main table and click Fix with AI. Proposals appear here for review before anything is pushed."); return; }
    body.innerHTML = `<table class="fx-table"><tr><th>URL</th><th>Kind</th><th>Current</th><th>Proposed (editable)</th><th>Why</th><th>Status</th><th></th></tr>${d.data.map((f) => `
      <tr class="${f.status}" data-id="${f.id}"><td class="u" title="${esc(f.url)}"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.url.replace(/^https?:\/\//, ""))}</a>${f.issue_name ? `<div class="muted">${esc(f.issue_name)}</div>` : ""}</td>
      <td>${esc(f.kind.replace("_", " "))}</td><td class="cur">${esc(f.current || "—")}</td>
      <td><textarea data-id="${f.id}" ${f.status === "pushed" ? "readonly" : ""}>${esc(f.proposed || "")}</textarea><div class="muted" style="font-size:11px">${(f.proposed || "").length} chars</div></td>
      <td class="rat">${esc(f.rationale || "")}${f.result ? `<div><b>${esc(f.target || "")}</b>: ${esc(f.result)}</div>` : ""}</td>
      <td><span class="st-pill ${f.status}">${esc(f.status)}</span></td>
      <td class="act">${f.status === "pushed" ? "" : `<button data-st="approved" title="Approve" aria-label="Approve">${ic("check")}</button><button data-st="rejected" title="Reject" aria-label="Reject">${ic("x")}</button>`}</td></tr>`).join("")}</table>
      <div class="pad muted">${fmtNum(d.total)} fixes${d.total > d.data.length ? ` (first ${d.data.length} shown)` : ""}</div>`;
    $$(".fx-table textarea", body).forEach((t) => t.onchange = async () => { await post("/api/fixes/update", { id: Number(t.dataset.id), proposed: t.value }); t.nextElementSibling.textContent = `${t.value.length} chars`; toast("Saved"); });
    $$(".fx-table [data-st]", body).forEach((b) => b.onclick = async () => { const id = Number(b.closest("tr").dataset.id); await post("/api/fixes/update", { id, status: b.dataset.st }); loadFixes(true); pollFixJob(); });
  }
  const shownIds = () => S.fixRows.map((f) => f.id);
  $("#fx-approve-shown").onclick = async () => { await post("/api/fixes/bulk", { ids: shownIds(), status: "approved" }); loadFixes(true); pollFixJob(); };
  $("#fx-reject-shown").onclick = async () => { await post("/api/fixes/bulk", { ids: shownIds(), status: "rejected" }); loadFixes(true); pollFixJob(); };
  $("#fx-delete-shown").onclick = (e) => {
    const b = e.target; if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Confirm delete"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Delete shown"; }, 4000); return; }
    post("/api/fixes/bulk", { ids: shownIds(), delete: true }).then(() => { b.dataset.armed = ""; b.textContent = "Delete shown"; loadFixes(); pollFixJob(); });
  };
  $("#fx-push").onclick = () => busy($("#fx-push"), async () => {
    const target = $("#fx-target").value;
    if (!target) { toast("Add a push target in Config → Connectors first", "warn"); return; }
    const approvedShown = S.fixRows.filter((f) => f.status === "approved").map((f) => f.id);
    try {
      const r = await post("/api/fixes/push", { target, ids: approvedShown, confirm: $("#fx-confirm").value });
      toast(`${r.env === "live" ? "LIVE" : "Staging"} push to ${r.target}: ${r.pushed} pushed, ${r.failed} failed, ${r.skipped} skipped`, r.failed ? "warn" : "info", 7000);
      $("#fx-confirm").value = ""; loadFixes(true); pollFixJob();
    } catch (err) { toast(err.message, "error", 7000); }
  });
  $("#config-reset").onclick = (e) => {
    const b = e.target;
    if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Reset everything? Click again"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Reset to defaults"; }, 4000); return; }
    b.dataset.armed = ""; b.textContent = "Reset to defaults";
    const keep = { ai_api_key: S.config.ai_api_key, targets: S.config.targets, ai_provider: S.config.ai_provider, ai_model: S.config.ai_model, ai_base_url: S.config.ai_base_url };
    renderConfig({ ...S.defaults, ...keep }); toast("Defaults restored (API key and connectors kept). Press Save to apply.", "warn");
  };
  $("#config-save").onclick = () => busy($("#config-save"), async () => {
    const r = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readConfig()) });
    if (r.status === 422) { showConfigErrors((await r.json()).errors || {}); return; }
    if (!r.ok) { toast("Could not save: " + r.statusText, "error"); return; }
    const bad = ($("#ai-model-status") || {}).classList?.contains("field-err");
    cfgLoaded = JSON.stringify(readConfig()); closeModal("modal-config"); await loadTabs();
    toast(bad ? "Configuration saved — note: the AI model name is not in the provider's list" : "Configuration saved", bad ? "warn" : "info");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = $$(".modal:not(.hidden)"); if (!open.length) return;
    const m = open[open.length - 1]; if (m.id === "modal-config") closeConfig(false); else m.classList.add("hidden");
  });

  // ------------------------------------------------------------------ crawls
  $("#btn-crawls").onclick = async () => {
    openModal("modal-crawls");
    const body = $("#crawls-body"); body.innerHTML = skel.list(6);
    const d = await api("/api/crawls");
    body.innerHTML = d.crawls.length ? d.crawls.map((c) => `<div class="crawl-row"><div class="info"><b>${esc(c.start_url || c.name)}</b><span class="muted">${esc(c.started_at)} · ${fmtNum(c.urls)} URLs · ${esc(c.state)} · ${(c.size / 1048576).toFixed(1)} MB${c.open ? " · open" : ""}</span></div><button data-open="${esc(c.path)}" class="primary">Open</button><button data-del="${esc(c.path)}">Delete</button></div>`).join("") : empty("folder", "No saved crawls yet", "Every crawl is saved automatically and will appear here.");
    const crawling = ["running", "paused", "stopping"].includes(S.status.state);
    $$("[data-open]", body).forEach((b) => { if (crawling) { b.disabled = true; b.title = "Stop the running crawl before opening another"; } });
    $$("[data-open]", body).forEach((b) => b.onclick = () => busy(b, async () => {
      try {
        closeModal("modal-crawls"); splash.show("Opening crawl…");
        await post("/api/crawls/open", { path: b.dataset.open });
        $$("#side-body")[0].dataset.loaded = "";
        await loadTabs(); selectTab("internal"); renderSide();
        toast("Crawl opened");
      } catch (e) { toast(e.message, "error"); } finally { splash.hide(); }
    }));
    $$("[data-del]", body).forEach((b) => b.onclick = async () => {
      if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Confirm delete"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Delete"; }, 4000); return; }
      try { await post("/api/crawls/delete", { path: b.dataset.del }); toast("Crawl deleted"); $("#btn-crawls").click(); } catch (e) { toast(e.message, "error"); }
    });
  };

  // ------------------------------------------------------------------ resizable panes
  function splitter(handle, target, axis, key, def, min, max) {
    const read = () => { try { return Number(localStorage.getItem(key)) || def; } catch (_) { return def; } };
    const apply = (v) => { v = Math.max(min, Math.min(max(), v)); target.style[axis === "y" ? "height" : "width"] = `${v}px`; try { localStorage.setItem(key, String(v)); } catch (_) {} if (S.table) S.table.redraw(true); return v; };
    apply(read());
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const start = axis === "y" ? e.clientY : e.clientX;
      const size = axis === "y" ? target.offsetHeight : target.offsetWidth;
      handle.classList.add("active"); document.body.classList.add("resizing"); document.body.style.cursor = axis === "y" ? "row-resize" : "col-resize";
      const move = (ev) => apply(size + (start - (axis === "y" ? ev.clientY : ev.clientX)));
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); handle.classList.remove("active"); document.body.classList.remove("resizing"); document.body.style.cursor = ""; if (S.table) S.table.redraw(true); };
      window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    });
    handle.addEventListener("dblclick", () => apply(def));
  }
  splitter($("#split-h"), $("#bottom"), "y", "cps.bottomHeight", 260, 60, () => window.innerHeight - 260);
  splitter($("#split-v"), $("#sidebar"), "x", "cps.sidebarWidth", 330, 180, () => window.innerWidth - 500);
  window.addEventListener("resize", () => { if (S.table) S.table.redraw(true); });

  // ------------------------------------------------------------------ boot
  (async () => {
    $("#tabs").innerHTML = skel.tabs();
    $("#side-body").innerHTML = skel.list(14);
    $("#bottom-body").innerHTML = empty("corner-left-up", "Select a URL", "Click a row in the table to see its details, inlinks, headers and source.");
    const t0 = Date.now();
    try {
      splash.show("Connecting to the crawler…");
      await Promise.all([loadReports(), poll()]);
      splash.show("Loading crawl data…");
      await loadTabs().catch(() => {});
      api("/api/config").then((d) => { S.config = d.config; S.defaults = d.defaults; S.userAgents = d.user_agents; }).catch(() => {});
      buildTable();
      renderSide();
    } finally {
      setTimeout(splash.hide, Math.max(0, 450 - (Date.now() - t0)));
    }
  })();
})();
