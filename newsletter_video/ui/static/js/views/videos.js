// Videos: the one page for the daily routine. Top to bottom: the default
// style for the automatic videos (set once, used by the scheduled run), then
// today's videos, each of which can try another style, preview it (a free
// draft) and be rendered. Trying whole new designs lives in the Design lab.
import { api } from "../api.js";
import { h, icon, spinner, formatBytes, formatClock, formatDay, plural } from "../dom.js";
import { costTag, toast } from "../components.js";
import { state, subscribe, isRunning, loadPlans, loadPlan, isTestRef, notify, defaultPresetId, effectivePresetId } from "../store.js";
import { planDay, runBatch, attempt, approxRender, WRITER_COST } from "../actions.js";
import { mountRunPanel } from "./run.js";
import { mountPlanPanel } from "./plan.js";
import { stylePicker, sameValue, describeValue, previewTile } from "../stylepicker.js";

export function mountVideos(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let todayKey = "";
  let starting = null; // "plan" | "batch" while the request is in flight
  let savingDefault = false;

  // ---- header + the flow ----
  const lede = h("p", { class: "lede" });
  const head = h("header", { class: "page-head" }, h("div", {}, h("h1", { text: "Videos" }), lede));
  const rail = h("ol", { class: "rail", "aria-label": "How it works" });
  const nextLine = h("p", { class: "rail-next", "aria-live": "polite" });

  // ---- default style for automatic videos ----
  const savedDefault = () => {
    const s = state.settings?.values || {};
    return { style: s.VIDEO_STYLE || "explainer", preset: effectivePresetId(s.LOOK_PRESET), bg: s.LOOK_BG || "", accent: s.LOOK_ACCENT || "", character: s.CHARACTER || "" };
  };
  let edited = savedDefault();
  const defaultHost = h("div");
  let picker = null;
  const defaultState = h("p", { class: "sp-state", role: "status", "aria-live": "polite" });
  const saveButton = h("button", { class: "btn primary", type: "button", onClick: saveDefault }, "Save as default");
  const resetButton = h("button", { class: "btn quiet", type: "button", onClick: () => { edited = savedDefault(); picker?.set(edited); paintDefault(); } }, "Undo changes");
  const defaultCard = h("section", { class: "panel default-style", "aria-labelledby": "default-h" },
    h("div", { class: "default-head" },
      h("div", {}, h("h2", { id: "default-h", text: "Default style for automatic videos" }),
        h("p", { class: "help default-sub" })),
      h("div", { class: "default-now" })),
    defaultHost,
    h("div", { class: "form-actions default-foot" }, saveButton, resetButton, defaultState));

  function paintDefault() {
    const sched = state.runs.schedule;
    const when = sched?.time ? `the ${formatClock(sched.time)} run` : "the scheduled run";
    defaultCard.querySelector(".default-sub").textContent = `Videos made by ${when} use this, and so does every new plan. A single video below can still try something else.`;
    const same = sameValue(edited, savedDefault());
    saveButton.disabled = same || savingDefault;
    resetButton.hidden = same;
    defaultState.textContent = same ? `Saved. Used by ${when}.` : "Not saved yet. Press Save as default to use it for automatic videos.";
    defaultState.classList.toggle("dirty", !same);
    const now = defaultCard.querySelector(".default-now");
    now.replaceChildren(previewTile(savedDefault(), { small: true }), h("span", {}, h("span", { class: "faint small", text: "Saved default" }), h("strong", { text: describeValue(savedDefault()) })));
  }

  async function saveDefault() {
    if (savingDefault) return;
    savingDefault = true;
    paintDefault();
    const e = edited;
    const updates = { VIDEO_STYLE: e.style, CHARACTER: e.character, LOOK_PRESET: e.preset === defaultPresetId() ? "" : e.preset, LOOK_BG: e.bg, LOOK_ACCENT: e.accent };
    const ok = await attempt(async () => {
      state.settings = await api.put("/api/settings", { updates });
      notify();
      toast("Saved. Automatic videos will use this style from the next run.");
    });
    savingDefault = false;
    if (ok && state.plan) loadPlan(state.plan.ref).catch(() => {});
    paintDefault();
  }

  function mountPicker() {
    picker?.dispose();
    edited = savedDefault();
    picker = stylePicker({ value: edited, onChange: (v) => { edited = v; paintDefault(); } });
    defaultHost.replaceChildren(picker);
    paintDefault();
  }
  onCleanup(() => picker?.dispose());

  // ---- today's videos: plan, run and tiles ----
  const planButton = h("button", { class: "btn primary", type: "button", onClick: startPlan }, icon("plus", "fill"), "Plan today's videos");
  const batchButton = h("button", { class: "btn-link", type: "button", onClick: startBatch }, "Run the full daily job now…");
  const planFacts = h("p", { class: "small faint" });
  const noticeHost = h("div", { class: "stack notices" });
  const emptyHost = h("div");
  const runHost = h("div");
  const planHost = h("div");
  const planBar = h("div", { class: "plan-bar" }, h("div", { class: "run-actions" }, planButton, batchButton), planFacts);

  // ---- rendered videos ----
  const todayHost = h("div");
  const todayTitle = h("h2", { text: "Rendered today", id: "today-h" });
  const todayMeta = h("p", { class: "small faint" });
  const earlierHost = h("div");
  const today = h("section", { class: "section", "aria-labelledby": "today-h" },
    h("div", { class: "today-head" }, todayTitle, todayMeta), todayHost, earlierHost);
  const labHint = h("p", { class: "help lab-hint" }, "Trying a new design, or want to see how a render looks without drafting a video? ", h("a", { class: "link", href: "#/lab", text: "Open the Design lab" }), ".");

  root.append(head, rail, nextLine, defaultCard, noticeHost, runHost,
    h("div", { class: "section" }, planBar, emptyHost, planHost), today, labHint);
  const runPanel = mountRunPanel(runHost, { accept: (job) => !job.meta?.test && !isTestRef(job.meta?.ref) });
  onCleanup(() => runPanel.dispose());
  onCleanup(mountPlanPanel(planHost, { onJobStarted: () => runPanel.focus() }));
  mountPicker();

  // ---- actions ----
  async function startPlan() {
    if (starting || isRunning()) return;
    starting = "plan";
    sync();
    const replacing = Boolean(state.plan && state.plan.ref === todayIso() && !state.plan.is_test);
    const ok = await attempt(async () => {
      if (await planDay(todayIso(), {}, { replacing })) runPanel.focus();
    });
    starting = null;
    sync();
    return ok;
  }

  async function startBatch() {
    if (starting || isRunning()) return;
    starting = "batch";
    sync();
    await attempt(async () => { if (await runBatch()) runPanel.focus(); });
    starting = null;
    sync();
  }

  // ---- rendering ----
  function sync() {
    const busy = isRunning() || starting;
    for (const b of [planButton, batchButton]) {
      b.disabled = Boolean(busy);
      b.title = busy ? "A job is already in progress" : "";
    }
    planButton.setAttribute("aria-busy", starting === "plan" ? "true" : "false");
    planButton.replaceChildren(starting === "plan" ? spinner() : icon("plus", "fill"), "Plan today's videos");

    const sv = state.settings?.values;
    if (sv) planFacts.replaceChildren(`${plural(Number(sv.VIDEOS_PER_DAY), "video")} · one writer call, `, costTag(WRITER_COST), ` · no rendering`);
    const todayPlan = state.plan && !isTestRef(state.plan.ref) && state.plan.ref === todayIso() ? state.plan : null;
    const planned = todayPlan ? todayPlan.videos.length : 0;
    const custom = todayPlan ? todayPlan.videos.filter((v) => Object.keys(v.look || {}).length).length : 0;
    const drafted = todayPlan ? todayPlan.videos.filter((v) => v.preview?.draft).length : 0;
    // the flow: pick a style, preview it, use it
    const items = [["Pick a style", "the default for automatic videos, or one video"], ["Preview", "a free draft, 30–60 s"], ["Use it", "save it, then render what you like"]];
    const current = !planned ? 1 : drafted ? 3 : 2;
    rail.replaceChildren(...items.map(([label, sub], i) => h("li", { dataset: { s: i + 1 < current ? "done" : i + 1 === current ? "current" : "todo" }, "aria-current": i + 1 === current ? "step" : null },
      h("span", { class: "rail-n", "aria-hidden": "true" }, i + 1 < current ? icon("check") : String(i + 1)),
      h("span", { class: "rail-text" }, h("strong", { text: label }), h("span", { class: "rail-sub", text: sub })))));
    const running = state.runs.latest?.state === "running" ? state.runs.latest : null;
    if (running) nextLine.replaceChildren("Running: ", h("strong", { text: running.stage || "starting" }));
    else if (!planned) nextLine.replaceChildren("Next: ", h("strong", { text: "plan today's videos" }), ` (one writer call, ${WRITER_COST}). Until you do, the default style above is what the automatic run will use.`);
    else if (!drafted) nextLine.replaceChildren("Next: open ", h("strong", { text: "Style for this video" }), " on any video and ", h("strong", { text: "preview" }), " it (free, 30–60 s), or leave it on the default.");
    else nextLine.replaceChildren("Next: happy with a preview? ", h("strong", { text: "Use this style for this video" }), ", then render what you like.");
    emptyHost.replaceChildren();
    if (!todayPlan && !running) {
      emptyHost.append(h("div", { class: "panel empty" }, h("h3", { text: "Nothing planned for today yet" }),
        h("p", { text: "Planning picks today's stories and writes the scripts, then stops. Nothing is voiced or rendered until you say so. Each video then gets a tile where you can try another style and preview it." })));
    }
    if (custom && planned) planFacts.append(` · ${plural(custom, "video")} with a custom style`);

    noticeHost.replaceChildren();
    const outside = state.runs.activity.find((a) => a.source !== "panel");
    if (outside) {
      noticeHost.append(h("div", { class: "notice warn" }, icon("info"),
        h("div", { class: "notice-body" }, h("p", { text: `${outside.label}.` }), h("p", { text: "Starting another is paused until it finishes." }))));
    }
    // Approval mode's hand-off: today's plan exists and some of it is still unrendered.
    const todayCard = (state.plans?.plans || []).find((p) => p.ref === todayIso() && !isTestRef(p.ref));
    const waiting = todayCard ? todayCard.count - (todayCard.rendered || 0) : 0;
    if (waiting > 0 && !isRunning()) {
      noticeHost.append(h("div", { class: "notice good", role: "status" }, icon("check"),
        h("div", { class: "notice-body" }, h("p", {}, h("strong", { text: "Plan ready: review and render" })),
          h("p", { text: `${plural(waiting, "video")} planned for today, not rendered yet (about ${approxRender(waiting)} to render). Check each one below, then press Render all or Render selected.` }))));
    }
    const sched = state.runs.schedule;
    lede.textContent = sched?.time
      ? `Plan, preview and render today's videos one step at a time. The daily job still runs by itself at ${formatClock(sched.time)} with your defaults.`
      : "Plan, preview and render today's videos one step at a time.";
    paintDefault();
  }

  // ---- plans ----
  async function refreshPlans(preferRef) {
    try {
      const data = await loadPlans();
      const refs = data.plans.filter((p) => !isTestRef(p.ref)).map((p) => p.ref);
      const latest = state.runs.latest;
      const fromJob = latest?.meta?.ref && refs.includes(latest.meta.ref) ? latest.meta.ref : null;
      const ref = preferRef && refs.includes(preferRef) ? preferRef
        : state.planRef && refs.includes(state.planRef) ? state.planRef
          : fromJob || (refs.includes(todayIso()) ? todayIso() : refs[0] || null);
      await loadPlan(ref);
    } catch { /* shown on the next poll */ }
  }

  // ---- rendered videos ----
  async function refreshVideos() {
    try {
      const data = await api.get("/api/videos");
      state.videos = data;
      const key = JSON.stringify([data.today, data.days]);
      if (key !== todayKey) { todayKey = key; renderVideos(data); }
    } catch { /* keep the previous list */ }
  }

  function renderVideos(data) {
    const day = data.today;
    todayTitle.textContent = `Rendered today, ${formatDay(day.date)}`;
    const total = day.videos.length;
    todayMeta.textContent = total ? plural(total, "video") : "";
    todayHost.replaceChildren();
    if (!total) {
      const sched = state.runs.schedule;
      todayHost.append(h("div", { class: "panel empty" },
        h("h3", { text: isRunning() ? "Videos will appear here as they finish" : "Nothing rendered yet today" }),
        h("p", { text: isRunning() ? "A job is in progress. This list updates by itself."
          : sched?.time ? `The scheduled run starts at ${formatClock(sched.time)}. Or plan today's videos above and render the ones you like.`
            : "Plan today's videos above, then render the ones you like." })));
    } else {
      todayHost.append(h("ul", { class: "panel vlist" }, day.videos.map((v) => videoRow(v))));
    }
    earlierHost.replaceChildren();
    const days = (data.days || []).filter((d) => d.videos.length);
    if (days.length) {
      const n = days.reduce((a, d) => a + d.videos.length, 0);
      earlierHost.append(h("details", { class: "earlier" },
        h("summary", {}, `Earlier videos`, h("span", { class: "faint", text: ` · ${plural(n, "video")} over ${plural(days.length, "day")}` })),
        ...days.map((d) => h("div", { class: "day" },
          h("h3", {}, formatDay(d.date), h("span", { class: "faint small", text: ` · ${plural(d.videos.length, "video")}` })),
          h("ul", { class: "panel vlist" }, d.videos.map((v) => videoRow(v)))))));
    }
  }

  function videoRow(v) {
    const li = h("li");
    const player = h("div", { class: "full", hidden: true });
    const watch = v.url ? h("button", { class: "btn small", type: "button", "aria-expanded": "false", onClick: () => {
      const open = player.hidden;
      player.hidden = !open;
      watch.setAttribute("aria-expanded", String(open));
      watch.replaceChildren(icon("play", "sm"), open ? "Hide" : "Watch");
      if (open && !player.firstChild) player.append(h("video", { src: v.url, controls: true, preload: "metadata", playsinline: true, class: "inline-player", "aria-label": v.title }));
      if (!open) player.querySelector("video")?.pause();
    } }, icon("play", "sm"), "Watch") : null;
    li.append(
      h("div", {}, h("div", { class: "t", text: v.title }),
        h("div", { class: "sub" }, h("span", { class: `chip ${v.status.tone === "neutral" ? "" : v.status.tone}`, text: v.status.state || "Ready" }),
          v.has_file ? h("span", { text: formatBytes(v.size) }) : h("span", { text: "File cleared" }),
          v.youtube ? h("span", { text: v.youtube.privacy ? `On YouTube, ${v.youtube.privacy}` : "On YouTube" }) : null),
        v.status.error ? h("div", { class: "errline", text: v.status.error }) : null),
      h("div", { class: "acts" }, watch,
        v.youtube ? h("a", { class: "btn small inline", href: v.youtube.url, target: "_blank", rel: "noopener noreferrer" }, "Open on YouTube", icon("link", "sm")) : null),
      player);
    return li;
  }

  // ---- wiring ----
  sync();
  todayHost.replaceChildren(h("div", { class: "panel empty" }, h("div", { class: "skeleton", "aria-hidden": "true" }), h("p", { class: "help", text: "Loading today's videos" })));
  refreshVideos();
  refreshPlans();
  const videosTimer = setInterval(refreshVideos, isRunning() ? 8000 : 20000);
  onCleanup(() => clearInterval(videosTimer));
  let lastRunKey = "";
  onCleanup(subscribe(() => {
    sync();
    const latest = state.runs.latest;
    const key = latest ? `${latest.id}:${latest.state}` : "";
    if (key !== lastRunKey) {
      const changed = lastRunKey !== "";
      lastRunKey = key;
      refreshVideos();
      if (changed && latest && ["plan", "rewrite", "preview", "render"].includes(latest.kind) && !latest.meta?.test) refreshPlans(latest.meta?.ref);
    }
  }));

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
