// Sebastian UI: no framework. Server-rendered pages, enhanced so they feel instant:
//  - links and form posts update the page in place (no reload, scroll position kept)
//  - Undo toasts, errors shown inside the dialog that caused them
//  - command palette (Cmd/Ctrl+K), live quick-add preview, keyboard shortcuts
// Everything falls back to normal navigation / form posts if anything here fails.
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const here = () => location.pathname + location.search;
  // history.state = { i: how many in-app pages precede this one, dlg: a dialog is open here }
  const depth = () => (history.state && history.state.i) || 0;
  const pushPath = (path) => history.pushState({ i: depth() + 1 }, "", path);
  const replacePath = (path) => history.replaceState({ i: depth() }, "", path);
  if (history.state && history.state.dlg) replacePath(here()); // reloaded with a dialog "open"
  const csrfToken = () => ($("input[name=csrf]") || {}).value || "";
  const NATIVE_PATHS = /^\/(static|docs|redoc|openapi\.json|api|mcp|health|favicon\.ico|apple-touch-icon\.png|login|logout)(\/|$)/;

  /* ------------------------------------------------------------------ progress + toasts */
  const progress = $("#progress");
  let progressTimer;
  function busy(on) {
    if (!progress) return;
    clearTimeout(progressTimer);
    if (on) {
      progress.className = "progress";
      void progress.offsetWidth;
      progress.classList.add("on");
    } else {
      progress.classList.remove("on");
      progress.classList.add("done");
      progressTimer = setTimeout(() => (progress.className = "progress"), 500);
    }
  }

  function dismissLater(t) {
    const ms = t.classList.contains("error") ? 9000 : t.querySelector("form") ? 8000 : 4200;
    setTimeout(() => {
      t.classList.add("leaving");
      setTimeout(() => t.remove(), 220);
    }, ms);
  }
  function addToast(node) {
    const box = $("#toasts");
    if (!box) return;
    const t = document.importNode(node, true);
    box.appendChild(t);
    dismissLater(t);
  }
  function toast(message, kind = "ok") {
    const t = document.createElement("div");
    t.className = "toast " + kind;
    const s = document.createElement("span");
    s.textContent = message;
    t.appendChild(s);
    addToast(t);
  }
  $$("#toasts .toast").forEach(dismissLater);
  const toastBox = $("#toasts");
  if (toastBox) toastBox.addEventListener("click", (e) => {
    const t = e.target.closest(".toast");
    if (t && !e.target.closest("form")) t.remove();
  });

  /* ------------------------------------------------------------------ in-place page updates */
  async function fetchPage(url, init) {
    busy(true);
    try {
      const res = await fetch(url, {
        credentials: "same-origin",
        ...init,
        headers: { "X-Requested-With": "fetch", ...((init && init.headers) || {}) },
      });
      return { res, type: res.headers.get("content-type") || "", text: await res.text() };
    } finally {
      busy(false);
    }
  }
  const isLoginPage = (res) => new URL(res.url).pathname === "/login";
  const parse = (html) => new DOMParser().parseFromString(html, "text/html");

  // Replace the parts of the page that change between screens. Returns false if the response
  // isn't a normal app page (then the caller falls back to a real navigation).
  function swapIn(doc) {
    const content = $(".content", doc);
    if (!content) return false;
    $$("dialog[open]").forEach((d) => d.close());
    $(".content").innerHTML = content.innerHTML;
    for (const sel of [".sidebar", "#dialogs"]) {
      const fresh = $(sel, doc), cur = $(sel);
      if (fresh && cur) cur.innerHTML = fresh.innerHTML;
    }
    document.title = doc.title;
    $$(".toast", $("#toasts", doc) || doc.createElement("div")).forEach(addToast);
    initRepeat();
    return true;
  }

  async function navigate(url, { push = true, scroll = true } = {}) {
    try {
      const { res, type, text } = await fetchPage(url);
      if (isLoginPage(res) && new URL(url, location.href).pathname !== "/login") return void location.assign("/login");
      if (!type.includes("text/html") || !swapIn(parse(text))) return void location.assign(url);
      const final = new URL(res.url);
      const path = final.pathname + final.search;
      if (push) pushPath(path);
      else if (path !== here()) replacePath(path);
      if (scroll) window.scrollTo(0, 0);
    } catch (_) {
      location.assign(url);
    }
  }
  // Back closes an open dialog first (phones), and only then leaves the page.
  let skipPop = false;
  let closingByBack = false;
  window.addEventListener("popstate", () => {
    if (skipPop) return void (skipPop = false);
    const open = $$("dialog[open]");
    if (open.length) {
      closingByBack = true;
      open.forEach((d) => d.close());
      closingByBack = false;
      return;
    }
    navigate(here(), { push: false, scroll: false });
  });
  // closing a dialog any other way (Esc, Cancel, backdrop) drops the history step it added
  document.addEventListener("close", (e) => {
    if (closingByBack || !e.target.dataset || !e.target.dataset.hist) return;
    delete e.target.dataset.hist;
    if (history.state && history.state.dlg) { skipPop = true; history.back(); }
  }, true);

  document.addEventListener("click", (e) => {
    // dialog openers
    const opener = e.target.closest("[data-dialog]");
    if (opener) return void openDialog(opener.dataset.dialog, opener);
    if (e.target.closest("[data-open-palette]")) return void openPalette();
    if (e.target.closest("[data-close]")) {
      const d = e.target.closest("dialog");
      return void (d && d.close());
    }
    if (e.target.tagName === "DIALOG") return void e.target.close(); // click on the backdrop
    $$("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
    if (e.target.closest("[data-nav-toggle]")) return void document.body.classList.toggle("nav-open");

    // "Back" on detail pages: return to wherever you came from (filters, scroll), else to the parent list
    const back = e.target.closest("[data-back]");
    if (back && e.button === 0 && !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)) {
      e.preventDefault();
      if (depth() > 0) history.back();
      else navigate(back.getAttribute("href"));
      return;
    }

    // same-origin links: swap instead of reloading
    const a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute("download") || a.hasAttribute("data-native")) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || NATIVE_PATHS.test(u.pathname)) return;
    e.preventDefault();
    document.body.classList.remove("nav-open");
    navigate(u.pathname + u.search);
  });

  /* ------------------------------------------------------------------ form posts, in place */
  function showError(dlg, message, row) {
    if (row) row.classList.remove("leaving");
    const body = dlg && $(".dlg-body", dlg);
    if (!body) return void toast(message, "error");
    let box = $(".form-error", dlg);
    if (!box) {
      box = document.createElement("div");
      box.className = "form-error";
      box.setAttribute("role", "alert");
      body.prepend(box);
    }
    box.textContent = message;
  }

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    const method = (form.method || "get").toLowerCase();
    if (e.defaultPrevented || form.hasAttribute("data-native") || !["get", "post"].includes(method)) return;
    const action = new URL(form.action, location.href);
    if (action.origin !== location.origin || /^\/(login|logout)$/.test(action.pathname)) return;
    e.preventDefault();
    if (method === "get") { // search and filter forms: update in place, one history step per search
      const q = new URLSearchParams(new FormData(form)).toString();
      return void navigate(action.pathname + (q ? "?" + q : ""));
    }
    if (form.dataset.busy) return;
    form.dataset.busy = "1";
    const dlg = form.closest("dialog");
    const row = form.closest(".row");
    const leaving = row && /\/(done|skip)$/.test(action.pathname);
    if (leaving) row.classList.add("leaving");
    const refocusQuickAdd = form.hasAttribute("data-quickadd");
    try {
      const [{ res, type, text }] = await Promise.all([
        fetchPage(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)) }),
        leaving ? new Promise((r) => setTimeout(r, 150)) : null,
      ]);
      if (isLoginPage(res)) return void location.assign("/login");
      if (!type.includes("text/html")) {
        let msg = "Something went wrong. Please try again.";
        try { msg = JSON.parse(text).detail || msg; } catch (_) { /* keep default */ }
        return void showError(dlg, msg, row);
      }
      const doc = parse(text);
      const err = $(".toast.error", doc);
      if (err && dlg) return void showError(dlg, err.textContent.trim(), row); // keep what they typed
      if (!swapIn(doc)) return void location.assign(res.url);
      const final = new URL(res.url);
      const path = final.pathname + final.search;
      // a dialog's own history step, or a deleted page, must not stay behind as a dead Back target
      if ((history.state && history.state.dlg) || /\/delete$/.test(action.pathname)) replacePath(path);
      else if (path !== here()) pushPath(path);
      if (refocusQuickAdd) { const q = $("#quickadd-input"); if (q) q.focus(); }
    } catch (_) {
      delete form.dataset.busy;
      form.submit(); // last resort: a normal, full-page post
      return;
    } finally {
      delete form.dataset.busy;
    }
  });

  /* ------------------------------------------------------------------ dialogs and menus */
  function openDialog(id, opener) {
    const dlg = document.getElementById(id);
    if (!dlg) return;
    const form = $("form", dlg);
    if (form && opener && opener.dataset.action) form.action = opener.dataset.action;
    const next = form && $("input[name=next]", form);
    if (next) next.value = (opener && opener.dataset.next) || here();
    if (opener) {
      $$("[data-fill]", dlg).forEach((el) => {
        const v = opener.dataset[el.dataset.fill];
        if (v !== undefined) el.textContent = v;
      });
      $$("[data-value]", dlg).forEach((el) => { el.value = opener.dataset[el.dataset.value] || ""; });
      $$("[data-check]", dlg).forEach((el) => { el.checked = !!opener.dataset[el.dataset.check]; });
    }
    $$(".form-error", dlg).forEach((x) => x.remove());
    $$("details.menu[open]").forEach((d) => d.removeAttribute("open"));
    if (!dlg.open) {
      dlg.showModal();
      if (id !== "delete-dialog") { // delete redirects away from the page it was opened on
        dlg.dataset.hist = "1";
        history.pushState({ i: depth() + 1, dlg: 1 }, "", here());
      }
    }
    const first = $("input:not([type=hidden]), select, textarea", dlg);
    if (first) first.focus();
  }

  function initRepeat() {
    $$("[data-repeat]").forEach((root) => {
      const sel = $("[name=repeat]", root);
      const sync = () => $$("[data-for]", root).forEach((el) =>
        el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(sel.value)));
      if (!root.dataset.bound) { sel.addEventListener("change", sync); root.dataset.bound = "1"; }
      sync();
    });
  }
  initRepeat();

  /* ------------------------------------------------------------------ quick-add live preview */
  let previewTimer, previewSeq = 0;
  document.addEventListener("input", (e) => {
    const input = e.target.closest("[data-quickadd] input[name=text]");
    if (!input) return;
    clearTimeout(previewTimer);
    const out = $("#qa-preview");
    if (!input.value.trim()) { if (out) out.textContent = ""; return; }
    previewTimer = setTimeout(async () => {
      const seq = ++previewSeq;
      try {
        const r = await fetch("/quick/preview?text=" + encodeURIComponent(input.value), { credentials: "same-origin" });
        if (!r.ok || seq !== previewSeq || !out) return;
        const d = await r.json();
        out.textContent = "";
        if (!d.title) return;
        out.append("Task ");
        const b = document.createElement("b");
        b.textContent = d.title;
        const w = document.createElement("span");
        w.className = "when";
        w.textContent = "· due " + d.when;
        out.append(b, " ", w);
      } catch (_) { /* preview is a nicety */ }
    }, 180);
  });

  /* ------------------------------------------------------------------ command palette */
  const PAGES = [
    { t: "Today", s: "G D", url: "/" }, { t: "Tasks", s: "G T", url: "/tasks" },
    { t: "Recurring tasks", s: "G R", url: "/series" }, { t: "Logs & notes", s: "G L", url: "/entries" },
    { t: "Categories", s: "G C", url: "/categories" }, { t: "Settings", s: "G S", url: "/settings" },
  ];
  const ACTIONS = [
    { t: "New task…", s: "N", run: () => openDialog("new-task-dialog") },
    { t: "Log an entry…", s: "L", run: () => openDialog("entry-dialog") },
    { t: "Keyboard shortcuts", s: "?", run: () => openDialog("shortcuts-dialog") },
  ];
  let items = [], sel = -1, searchSeq = 0, searchTimer;

  function openPalette() {
    const dlg = $("#palette");
    if (!dlg) return;
    $$("details.menu[open]").forEach((d) => d.removeAttribute("open"));
    if (!dlg.open) dlg.showModal();
    const q = $("#palette-q");
    q.value = "";
    build("");
    q.focus();
  }

  function build(q, remote = []) {
    const needle = q.trim().toLowerCase();
    const match = (x) => x.t.toLowerCase().includes(needle);
    const item = (x, title) => ({ ...x, title: title || x.t, sub: x.s });
    const rows = [];
    const section = (group, list) => { if (list.length) rows.push({ group }, ...list); };
    if (!needle) {
      section("Actions", ACTIONS.map((a) => item(a)));
      section("Go to", PAGES.map((p) => item(p)));
    } else {
      // search hits from the server, grouped under their own headings (Tasks, Recurring, ...)
      for (const g of [...new Set(remote.map((r) => r.group))]) section(g, remote.filter((r) => r.group === g));
      section("Actions", ACTIONS.filter(match).map((a) => item(a)));
      section("Go to", PAGES.filter(match).map((p) => item(p, "Go to " + p.t)));
      rows.push({ group: "Quick add" }, { title: `Add task “${q.trim()}”`, sub: "arrow keys, then ↵", add: q.trim() });
    }
    render(rows);
  }

  function render(rows) {
    const list = $("#palette-list");
    list.textContent = "";
    items = [];
    for (const r of rows) {
      if (r.group && !r.title) {
        const g = document.createElement("li");
        g.className = "grp";
        g.textContent = r.group;
        list.appendChild(g);
        continue;
      }
      const li = document.createElement("li");
      li.className = "item";
      li.setAttribute("role", "option");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = r.title;
      const s = document.createElement("span");
      s.className = "s";
      s.textContent = r.sub || "";
      li.append(t, s);
      li.addEventListener("mousemove", () => select(items.indexOf(r)));
      li.addEventListener("click", () => choose(r));
      r.el = li;
      items.push(r);
      list.appendChild(li);
    }
    // default selection: the first real result, never the "add" row (so Enter can't create by accident)
    sel = items.findIndex((i) => !i.add);
    if (sel < 0) sel = -1;
    paint();
  }

  function paint() {
    items.forEach((it, i) => it.el.setAttribute("aria-selected", i === sel ? "true" : "false"));
    const cur = items[sel];
    if (cur) cur.el.scrollIntoView({ block: "nearest" });
  }
  function select(i) {
    if (i < 0 || i === sel) return;
    sel = i;
    paint();
  }

  function choose(it) {
    if (!it) return;
    const dlg = $("#palette");
    if (it.add) {
      const f = $("#palette-add");
      $("input[name=text]", f).value = it.add;
      $("input[name=next]", f).value = here();
      dlg.close();
      return void f.requestSubmit();
    }
    dlg.close();
    if (it.run) return void it.run();
    if (it.url) navigate(it.url);
  }

  document.addEventListener("input", (e) => {
    if (e.target.id !== "palette-q") return;
    const q = e.target.value;
    build(q);
    clearTimeout(searchTimer);
    if (q.trim().length < 2) return;
    searchTimer = setTimeout(async () => {
      const seq = ++searchSeq;
      try {
        const r = await fetch("/search.json?q=" + encodeURIComponent(q.trim()), { credentials: "same-origin" });
        if (!r.ok || seq !== searchSeq) return;
        const d = await r.json();
        if (seq === searchSeq && $("#palette-q").value === q) build(q, (d.results || []).map((x) => ({ ...x, title: x.title, sub: x.sub })));
      } catch (_) { /* offline or logged out: local results remain */ }
    }, 150);
  });

  document.addEventListener("keydown", (e) => {
    const pal = $("#palette");
    if (pal && pal.open) {
      if (e.key === "ArrowDown") { e.preventDefault(); select(Math.min(items.length - 1, sel + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); select(Math.max(0, sel - 1)); }
      else if (e.key === "Enter") { e.preventDefault(); choose(items[sel]); }
      return;
    }
    if (e.key === "Escape") $$("details.menu[open]").forEach((d) => d.removeAttribute("open"));
  });

  /* ------------------------------------------------------------------ keyboard shortcuts */
  let gPending = false, gTimer;
  const GO = { d: "/", t: "/tasks", r: "/series", l: "/entries", c: "/categories", s: "/settings" };
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); return void openPalette(); }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const el = document.activeElement;
    const typing = el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
    if (typing || $("dialog[open]")) return;
    if (gPending) {
      gPending = false;
      const dest = GO[e.key.toLowerCase()];
      if (dest) { e.preventDefault(); navigate(dest); }
      return;
    }
    switch (e.key) {
      case "n": e.preventDefault(); openDialog("new-task-dialog"); break;
      case "l": e.preventDefault(); openDialog("entry-dialog"); break;
      case "/": e.preventDefault(); openPalette(); break;
      case "?": e.preventDefault(); openDialog("shortcuts-dialog"); break;
      case "g": gPending = true; clearTimeout(gTimer); gTimer = setTimeout(() => (gPending = false), 1200); break;
    }
  });
})();
