// Sebastian UI: dialogs, menus, repeat picker, toasts. Progressive enhancement only.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const here = () => location.pathname + location.search;

  // --- open dialogs from buttons: <button data-dialog="snooze-dialog" data-action="/tasks/7/snooze" data-title="...">
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-dialog]");
    if (opener) {
      const dlg = document.getElementById(opener.dataset.dialog);
      if (!dlg) return;
      const form = $("form", dlg);
      if (opener.dataset.action) form.action = opener.dataset.action;
      const next = $("input[name=next]", form);
      if (next) next.value = opener.dataset.next || here();
      $$("[data-fill]", dlg).forEach((el) => {
        const v = opener.dataset[el.dataset.fill];
        if (v !== undefined) el.textContent = v;
      });
      $$("[data-value]", dlg).forEach((el) => { el.value = opener.dataset[el.dataset.value] || ""; });
      $$("[data-check]", dlg).forEach((el) => { el.checked = !!opener.dataset[el.dataset.check]; });
      $$("details.menu[open]").forEach((d) => d.removeAttribute("open"));
      dlg.showModal();
      const first = $("input:not([type=hidden]), select, textarea", dlg);
      if (first) first.focus();
      return;
    }
    if (e.target.closest("[data-close]")) {
      const dlg = e.target.closest("dialog");
      if (dlg) dlg.close();
      return;
    }
    if (e.target.tagName === "DIALOG") e.target.close();  // click on backdrop
    // close open menus on outside click
    $$("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
    if (e.target.closest("[data-nav-toggle]")) document.body.classList.toggle("nav-open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$("details.menu[open]").forEach((d) => d.removeAttribute("open"));
  });

  // --- repeat picker: show only the fields relevant to the chosen kind
  function syncRepeat(root) {
    const kind = $("[name=repeat]", root).value;
    $$("[data-for]", root).forEach((el) => {
      el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(kind));
    });
  }
  $$("[data-repeat]").forEach((root) => {
    $("[name=repeat]", root).addEventListener("change", () => syncRepeat(root));
    syncRepeat(root);
  });

  // --- toasts from server-side flash messages
  const box = $("#toasts");
  if (box) {
    $$(".toast", box).forEach((t) => setTimeout(() => t.remove(), t.classList.contains("error") ? 9000 : 4200));
    box.addEventListener("click", (e) => { const t = e.target.closest(".toast"); if (t) t.remove(); });
  }

  // --- keep submit buttons from double-posting
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (f.dataset.busy) { e.preventDefault(); return; }
    f.dataset.busy = "1";
    setTimeout(() => delete f.dataset.busy, 4000);
  });
})();
