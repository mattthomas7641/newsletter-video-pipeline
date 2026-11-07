// Today's videos: plan the day, review and render it, see what was made.
// Trying a look happens in the workbench; this page is the daily routine.
import { api } from "../api.js";
import { h, icon, spinner, formatBytes, formatClock, formatDay, plural } from "../dom.js";
import { costTag } from "../components.js";
import { state, subscribe, value, isDirty, isRunning, draftLook, loadPlans, loadPlan, loadStyles, isTestRef } from "../store.js";
import { planDay, runBatch, attempt, approxRender, WRITER_COST } from "../actions.js";
import { mountRunPanel } from "./run.js";
import { mountPlanPanel, lookChips } from "./plan.js";

export function mountDaily(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let todayKey = "";
  let starting = null; // "plan" | "batch" while the request is in flight

  // ---- header ----
  const lede = h("p", { class: "lede" });
  const head = h("header", { class: "page-head" }, h("div", {}, h("h1", { text: "Today's videos" }), lede));

  // ---- start panel ----
  const planButton = h("button", { class: "btn primary", type: "button", onClick: startPlan }, icon("plus", "fill"), "Plan today's videos");
  const batchButton = h("button", { class: "btn-link", type: "button", onClick: startBatch }, "Run the full daily job now…");
  const summary = h("div", { class: "summary", "aria-label": "Default look for new plans" });
  const unsavedNote = h("p", { class: "small faint", hidden: true, text: "Includes your unsaved look changes." });
  const planFacts = h("p", { class: "small faint" });
  const noticeHost = h("div", { class: "stack notices" });
  const make = h("section", { class: "panel make", "aria-label": "Start" },
    h("div", {}, h("h2", { text: "Plan first, render last" }),
      h("p", { text: "Picks the stories and writes the scripts, then stops. Nothing is voiced or rendered until you say so." }),
      summary, unsavedNote, h("span", { class: "spacer" }), planFacts, h("div", { class: "run-actions" }, planButton, batchButton)),
    h("div", { class: "make-aside" }, h("h2", { text: "Trying a new look?" }),
      h("p", { text: "Drop in a reference video, make a throwaway test video in its style and tweak it side by side. Nothing there is uploaded or counted." }),
      h("span", { class: "spacer" }),
      h("a", { class: "btn", href: "#/workbench" }, icon("palette", "sm"), "Open the Look workbench", icon("arrow", "sm"))));

  // ---- run + plan panels ----
  const runHost = h("div");
  const planHost = h("div");

  // ---- rendered videos ----
  const todayHost = h("div");
  const todayTitle = h("h2", { text: "Rendered today", id: "today-h" });
  const todayMeta = h("p", { class: "small faint" });
  const earlierHost = h("div");
  const today = h("section", { class: "section", "aria-labelledby": "today-h" },
    h("div", { class: "today-head" }, todayTitle, todayMeta), todayHost, earlierHost);

  root.append(head, make, noticeHost, runHost, planHost, today);
  const runPanel = mountRunPanel(runHost, { accept: (job) => !job.meta?.test && !isTestRef(job.meta?.ref) && job.kind !== "style-add" });
  onCleanup(() => runPanel.dispose());
  onCleanup(mountPlanPanel(planHost, { onJobStarted: () => runPanel.focus() }));

  // ---- actions ----
  async function startPlan() {
    if (starting || isRunning()) return;
    starting = "plan";
    sync();
    const replacing = Boolean(state.plan && state.plan.ref === todayIso() && !state.plan.is_test);
    const ok = await attempt(async () => {
      if (await planDay(todayIso(), draftLook(), { replacing })) runPanel.focus();
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

    const resolved = { style: value("VIDEO_STYLE"), character: value("CHARACTER"), theme: value("VIDEO_THEME"),
      background: value("BACKGROUND_STYLE"), recipe: value("STYLE_RECIPE") };
    summary.replaceChildren(...lookChips(resolved, {}, state.plans?.recipes));
    unsavedNote.hidden = !isDirty();
    const s = state.settings?.values;
    if (s) {
      planFacts.replaceChildren(`${plural(Number(s.VIDEOS_PER_DAY), "video")} · one writer call, `, costTag(WRITER_COST), ` · no rendering`);
    }

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
  if (!state.styles) loadStyles().catch(() => {});
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
