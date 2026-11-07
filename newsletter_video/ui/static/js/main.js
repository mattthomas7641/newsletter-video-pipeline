import { h } from "./dom.js";
import { mountSaveBar } from "./components.js";
import { state, subscribe, loadAll, refreshRuns, isRunning, isDirty } from "./store.js";
import { mountDaily } from "./views/daily.js";
import { mountWorkbench } from "./views/workbench.js";
import { mountSettings } from "./views/settings.js";

const ROUTES = {
  "/": { name: "daily", title: "Today's videos", mount: mountDaily },
  "/workbench": { name: "workbench", title: "Look workbench", mount: mountWorkbench },
  "/settings": { name: "settings", title: "Settings", mount: mountSettings },
};
// Older bookmarks and links keep working.
const LEGACY = { "/styles": "/workbench", "/look": "/settings", "/videos": "/", "/daily": "/" };

const viewHost = document.getElementById("view");
let disposeView = null;
let currentRoute = null;

function route() {
  let path = location.hash.replace(/^#/, "") || "/";
  if (LEGACY[path]) { path = LEGACY[path]; history.replaceState(null, "", `#${path}`); }
  return ROUTES[path] ? path : "/";
}

function show() {
  const path = route();
  const def = ROUTES[path];
  if (disposeView) { disposeView(); disposeView = null; }
  for (const link of document.querySelectorAll(".nav a")) {
    if (link.dataset.route === def.name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${def.title} - ByteSize Control Panel`;
  viewHost.replaceChildren();
  if (state.loadError) { showLoadError(); return; }
  disposeView = def.mount(viewHost, {}) || null;
  if (currentRoute !== null && currentRoute !== path) {
    const main = document.getElementById("main");
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }
  currentRoute = path;
}

function showLoadError() {
  viewHost.replaceChildren(h("div", { class: "panel empty" },
    h("h3", { text: "Can't reach the control panel" }),
    h("p", { text: state.loadError }),
    h("button", { class: "btn primary", type: "button", onClick: () => location.reload() }, "Try again")));
}

// ---- sidebar footer: what's running, or whether setup needs attention ----
const sideFoot = document.getElementById("side-foot");
let sideKey = "";
function renderSideFoot() {
  let kind = "good", title = "Ready", text = "Nothing is running.", href = "#/";
  const latest = state.runs.latest;
  if (isRunning()) {
    kind = "busy";
    title = { batch: "Daily batch running", test: "Test video running", plan: latest?.meta?.test ? "Planning a test video" : "Planning",
      preview: "Preview rendering", render: "Final render running", rewrite: "Rewriting a script" }[latest?.kind] || "A job is in progress";
    text = latest?.stage || "Working";
    href = latest?.meta?.test ? "#/workbench" : "#/";
  } else if (isDirty()) {
    kind = "warn"; title = "Unsaved changes"; text = "Save to use them in the next run."; href = "#/settings";
  } else if (state.setup && state.setup.checks.some((c) => !c.ok && !c.optional)) {
    kind = "warn"; title = "Setup needs attention"; text = "See Settings."; href = "#/settings";
  }
  const key = `${kind}|${title}|${text}|${href}`;
  if (key === sideKey) return;
  sideKey = key;
  sideFoot.replaceChildren(h("a", { class: `side-note ${kind}`, href },
    h("span", { class: "dot" }), h("span", {}, h("strong", { text: title }), h("span", { class: "side-text", text }))));
}
subscribe(renderSideFoot);

// ---- polling: quick while something runs, relaxed otherwise ----
let lastFailure = 0;
async function poll() {
  try {
    await refreshRuns();
    lastFailure = 0;
  } catch {
    lastFailure += 1;
  }
  setTimeout(poll, isRunning() ? 1500 : lastFailure ? 8000 : 5000);
}

mountSaveBar(document.getElementById("savebar-slot"));
window.addEventListener("hashchange", show);
window.addEventListener("beforeunload", (event) => {
  if (isDirty()) { event.preventDefault(); event.returnValue = ""; }
});

await loadAll();
renderSideFoot();
show();
poll();
