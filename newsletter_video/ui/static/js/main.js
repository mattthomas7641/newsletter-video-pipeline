import { h, icon } from "./dom.js";
import { mountSaveBar } from "./components.js";
import { state, subscribe, loadAll, refreshRuns, isRunning, isDirty } from "./store.js";
import { mountHome } from "./views/home.js";
import { mountLook } from "./views/look.js";
import { mountSettings } from "./views/settings.js";
import { mountVideos } from "./views/videos.js";

const ROUTES = {
  "/": { name: "home", title: "Studio", mount: mountHome },
  "/look": { name: "look", title: "Look and voice", mount: mountLook },
  "/settings": { name: "settings", title: "Settings", mount: mountSettings },
  "/videos": { name: "videos", title: "Videos", mount: mountVideos },
};

const viewHost = document.getElementById("view");
let disposeView = null;
let currentRoute = null;

function route() {
  const path = location.hash.replace(/^#/, "") || "/";
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
  let kind = "good", title = "Ready", text = "Nothing is running.";
  const latest = state.runs.latest;
  if (isRunning()) {
    kind = "busy";
    title = latest?.kind === "batch" ? "Daily batch running" : latest?.kind === "test" ? "Test video running" : "A run is in progress";
    text = latest?.stage || "Working";
  } else if (isDirty()) {
    kind = "warn"; title = "Unsaved changes"; text = "Save to use them in the next run.";
  } else if (state.setup && state.setup.checks.some((c) => !c.ok && !c.optional)) {
    kind = "warn"; title = "Setup needs attention"; text = "See Settings.";
  }
  const key = `${kind}|${title}|${text}`;
  if (key === sideKey) return;
  sideKey = key;
  sideFoot.replaceChildren(h("a", { class: `side-note ${kind}`, href: kind === "warn" && !isDirty() ? "#/settings" : "#/" },
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
