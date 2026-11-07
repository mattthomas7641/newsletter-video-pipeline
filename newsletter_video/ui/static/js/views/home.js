import { api, ApiError } from "../api.js";
import { h, icon, spinner, formatDuration, formatBytes, formatClock, formatDay, plural } from "../dom.js";
import { toast } from "../components.js";
import { state, subscribe, value, isDirty, refreshRuns, isRunning, draftLook, loadPlans, loadPlan } from "../store.js";
import { confirmDialog, mountPlanPanel, lookChips, formatDate } from "./plan.js";

const FINISHED_VISIBLE_MS = 6 * 60 * 60 * 1000;
const DISMISS_KEY = "bytesize.dismissedRun";
const readDismissed = () => { try { return localStorage.getItem(DISMISS_KEY); } catch { return null; } };
const writeDismissed = (id) => { try { localStorage.setItem(DISMISS_KEY, id); } catch { /* storage unavailable */ } };

const KIND_TITLES = {
  plan: "Planning", rewrite: "Rewriting a script", preview: "Preview", render: "Final render",
  batch: "Today's batch", test: "Test video",
};

export function mountHome(root, ctx) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let todayKey = "";
  let resultKey = "";
  let startError = null;
  let starting = null; // "plan" | "test" | "batch" while the request is in flight
  let detailsOpen = false;

  // ---- header ----
  const lede = h("p", { class: "lede" });
  const head = h("header", { class: "page-head" }, h("div", {}, h("h1", { text: "Studio" }), lede));

  // ---- start panel ----
  const planButton = h("button", { class: "btn primary", type: "button", onClick: () => startPlan(false) }, icon("plus", "fill"), "Plan today's videos");
  const testButton = h("button", { class: "btn", type: "button", onClick: () => startPlan(true) }, "Plan a test video");
  const batchButton = h("button", { class: "btn-link", type: "button", onClick: startBatch }, "Run the full daily job now…");
  const summary = h("div", { class: "summary", "aria-label": "Default look for new plans" });
  const unsavedNote = h("p", { class: "small faint", hidden: true, text: "Includes your unsaved Look changes." });
  const planFacts = h("p", { class: "small faint" });
  const startErrorHost = h("div", { class: "section" });
  const make = h("section", { class: "panel make", "aria-label": "Start" },
    h("div", {}, h("h2", { text: "Today's plan" }),
      h("p", { text: "Picks the stories and writes the scripts, then stops. Nothing is voiced or rendered until you say so, so you can check each video's look first." }),
      summary, unsavedNote, h("span", { class: "spacer" }), planFacts, h("div", { class: "run-actions" }, planButton, batchButton)),
    h("div", {}, h("h2", { text: "Test plan" }),
      h("p", { text: "One throwaway video to try a look on. It is never uploaded and never counts as a story you have covered." }),
      h("span", { class: "spacer" }), h("p", { class: "small faint", text: "One writer call, about 3 cents." }), testButton));

  // ---- run panel ----
  const runTitle = h("h2", { tabindex: -1 });
  const runChip = h("span", { class: "chip" });
  const runElapsed = h("span", { class: "small faint tnum" });
  const dismissButton = h("button", { class: "btn quiet small", type: "button", hidden: true, onClick: dismissRun }, "Dismiss");
  const steps = h("ol", { class: "steps", "aria-label": "Progress" });
  const stageLine = h("strong");
  const lastLine = h("span", { class: "last" });
  const stage = h("div", { class: "stage", "aria-live": "polite" }, stageLine, lastLine);
  const stopButton = h("button", { class: "btn small danger", type: "button", onClick: stopRun }, icon("stop", "sm"), "Stop");
  const detailsButton = h("button", { class: "btn small quiet", type: "button", "aria-expanded": "false", onClick: toggleDetails }, "Show details");
  const logBox = h("pre", { class: "log", hidden: true, tabindex: 0, "aria-label": "Run log" });
  const resultHost = h("div");
  const runActions = h("div", { class: "run-actions" }, stopButton, detailsButton);
  const runBody = h("div", { class: "run-body" }, steps, stage, resultHost, runActions, logBox);
  const run = h("section", { class: "panel run", hidden: true, "aria-labelledby": "run-title" },
    h("div", { class: "run-head" }, runTitle, runChip, runElapsed, dismissButton), runBody);
  runTitle.id = "run-title";

  // ---- plan panel ----
  const planHost = h("div");

  // ---- today ----
  const todayHost = h("div");
  const todayTitle = h("h2", { text: "Today" });
  const todayMeta = h("p", { class: "small faint" });
  const today = h("section", { class: "section", "aria-labelledby": "today-h" },
    h("div", { class: "today-head" }, todayTitle, todayMeta), todayHost);
  todayTitle.id = "today-h";

  root.append(head, make, startErrorHost, run, planHost, today);
  onCleanup(mountPlanPanel(planHost, { onJobStarted: () => { runTitle.focus(); run.scrollIntoView({ block: "nearest", behavior: "smooth" }); } }));

  // ---- actions ----
  async function startPlan(test) {
    if (starting || isRunning()) return;
    startError = null;
    starting = test ? "test" : "plan";
    sync();
    try {
      const body = { ref: test ? null : todayIso(), overrides: draftLook() };
      if (!test && state.plan && state.plan.ref === todayIso() && !state.plan.is_test) {
        const ok = await confirmDialog({
          title: "Plan today again?",
          body: h("p", { class: "muted", text: "Today already has a plan. Planning again replaces it with new story picks and scripts (one writer call). Videos already rendered are kept." }),
          confirmLabel: "Plan again",
        });
        if (!ok) return;
      }
      await api.post("/api/plans", body);
      detailsOpen = false;
      await refreshRuns();
      runTitle.focus();
      run.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (err) {
      startError = err.message;
      if (err instanceof ApiError && err.status === 409) refreshRuns().catch(() => {});
    } finally {
      starting = null;
      sync();
    }
  }

  async function startBatch() {
    if (starting || isRunning()) return;
    const n = Number(state.settings.values.VIDEOS_PER_DAY);
    const uploads = state.settings.values.YOUTUBE_UPLOAD === "1";
    const privacy = state.settings.values.YOUTUBE_PRIVACY;
    const ok = await confirmDialog({
      title: "Run the full daily job now?",
      body: h("div", { class: "stack" },
        h("p", { class: "muted", text: "This is the unattended 7:00 AM job: it plans, voices and renders everything in one go, with no review step." }),
        h("ul", {},
          h("li", { text: `Makes ${plural(n, "video")} at full quality. About ${approx(n)}.` }),
          h("li", { text: uploads ? `Uploads them to YouTube as ${privacy}.` : "Doesn't upload them to YouTube." }),
          h("li", { text: "Uses your saved settings and ignores any plan on this page." })),
        isDirty() ? h("p", { class: "help", text: "You have unsaved changes. They are not included. Save them first if you want them used." }) : null,
        uploads ? h("p", { class: "help", text: "This is the real thing: it posts to your channel." }) : null),
      confirmLabel: "Run daily job",
      tone: uploads ? "danger" : "primary",
    });
    if (!ok) return;
    startError = null;
    starting = "batch";
    sync();
    try {
      await api.post("/api/runs", { kind: "batch", confirm: true });
      await refreshRuns();
      runTitle.focus();
      run.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (err) {
      startError = err.message;
    } finally {
      starting = null;
      sync();
    }
  }

  async function stopRun() {
    const latest = state.runs.latest;
    if (!latest || latest.state !== "running") return;
    const ok = await confirmDialog({
      title: "Stop this job?",
      body: h("p", { class: "muted", text: latest.kind === "batch" || latest.kind === "render"
        ? "Videos already made are kept. Anything not yet made is skipped."
        : "It stops where it is. Nothing else is affected." }),
      confirmLabel: "Stop", tone: "danger",
    });
    if (!ok) return;
    try { await api.post(`/api/jobs/${latest.id}/cancel`); } catch (err) { toast(err.message, "bad"); }
    refreshRuns().catch(() => {});
  }

  function dismissRun() {
    if (state.runs.latest) writeDismissed(state.runs.latest.id);
    sync();
  }

  function toggleDetails() {
    detailsOpen = !detailsOpen;
    syncDetails();
  }
  function syncDetails() {
    logBox.hidden = !detailsOpen;
    detailsButton.textContent = detailsOpen ? "Hide details" : "Show details";
    detailsButton.setAttribute("aria-expanded", String(detailsOpen));
    if (detailsOpen) logBox.scrollTop = logBox.scrollHeight;
  }

  // ---- rendering ----
  const STEP_LABELS = ["Newsletter", "Scripts", "Voice and video", "Done"];

  function syncMake() {
    const busy = isRunning() || starting;
    for (const b of [planButton, testButton, batchButton]) {
      b.disabled = Boolean(busy);
      b.title = busy ? "A job is already in progress" : "";
    }
    planButton.setAttribute("aria-busy", starting === "plan" ? "true" : "false");
    planButton.replaceChildren(starting === "plan" ? spinner() : icon("plus", "fill"), "Plan today's videos");
    testButton.replaceChildren(...(starting === "test" ? [spinner()] : []), "Plan a test video");

    const resolved = { style: value("VIDEO_STYLE"), character: value("CHARACTER"), theme: value("VIDEO_THEME"),
      background: value("BACKGROUND_STYLE"), recipe: value("STYLE_RECIPE") };
    summary.replaceChildren(...lookChips(resolved, {}, state.plans?.recipes));
    unsavedNote.hidden = !isDirty();
    const s = state.settings?.values;
    if (s) planFacts.textContent = `${plural(Number(s.VIDEOS_PER_DAY), "video")}, one writer call (about 3 cents), no rendering.`;

    startErrorHost.replaceChildren();
    if (startError) {
      startErrorHost.append(h("div", { class: "notice bad", role: "alert" }, icon("alert"),
        h("div", { class: "notice-body" }, h("p", { text: startError }))));
    }
    const outside = state.runs.activity.find((a) => a.source !== "panel");
    if (outside && !startError) {
      startErrorHost.append(h("div", { class: "notice warn" }, icon("info"),
        h("div", { class: "notice-body" }, h("p", { text: `${outside.label}.` }), h("p", { text: "Starting another is paused until it finishes." }))));
    }
    const sched = state.runs.schedule;
    lede.textContent = sched?.time
      ? `Plan, preview and render today's videos one step at a time. The daily job still runs by itself at ${formatClock(sched.time)} with your defaults.`
      : "Plan, preview and render today's videos one step at a time.";
  }

  function syncRun() {
    const latest = state.runs.latest;
    const visible = latest && (latest.state === "running" ||
      ((Date.now() / 1000 - (latest.finished_at || 0)) * 1000 < FINISHED_VISIBLE_MS && readDismissed() !== latest.id));
    run.hidden = !visible;
    if (!visible) return;

    runTitle.textContent = runLabel(latest);
    const stateInfo = {
      running: ["active", "Running"], done: ["good", "Finished"], failed: ["bad", "Didn't finish"],
      cancelled: ["", "Stopped"], interrupted: ["warn", "Lost track"],
    }[latest.state] || ["", latest.state];
    runChip.className = `chip ${stateInfo[0]}`;
    runChip.replaceChildren(...[latest.state === "running" ? spinner() : latest.state === "done" ? icon("check") : null, stateInfo[1]].filter(Boolean));
    dismissButton.hidden = latest.state === "running";
    stopButton.hidden = latest.state !== "running";

    const stepped = latest.kind === "batch" || latest.kind === "test" || latest.kind === "render";
    steps.hidden = !stepped;
    const failed = latest.state === "failed" || latest.state === "interrupted";
    if (stepped) {
      const uploads = latest.meta?.uploads;
      const labels = latest.kind === "render" ? ["Plan", "Scripts", "Voice and video", uploads ? "Upload" : "Done"]
        : [...STEP_LABELS.slice(0, 3), uploads ? "Upload" : "Done"];
      const current = latest.state === "done" ? 5 : latest.kind === "render" ? Math.max(3, latest.step || 3) : Math.max(1, latest.step || 1);
      steps.className = `steps${failed ? " failed" : ""}`;
      steps.replaceChildren(...labels.map((label, i) => h("li", {
        dataset: { s: i + 1 < current ? "done" : i + 1 === current ? "current" : "todo" },
        "aria-current": i + 1 === current && latest.state === "running" ? "step" : null,
      }, label)));
    }

    if (latest.state === "running") {
      stageLine.textContent = latest.stage || "Starting up";
      lastLine.textContent = latest.last_line || "";
    } else if (latest.state === "done") {
      stageLine.textContent = {
        plan: "The plan is ready. Review it below.", rewrite: "The script was rewritten.", preview: "The preview is ready, in the plan below.",
        render: "The render finished.", batch: "The batch finished.", test: "Your test video is ready.",
      }[latest.kind] || "Finished.";
      lastLine.textContent = "";
    } else if (latest.state === "cancelled") {
      stageLine.textContent = "You stopped this job.";
      lastLine.textContent = "";
    } else {
      stageLine.textContent = latest.state === "interrupted"
        ? "The panel lost track of this job, for example after a restart. It may have finished or been stopped."
        : "This job stopped with an error.";
      lastLine.textContent = "";
    }

    if (state.log.jobId === latest.id && logBox.textContent !== state.log.text) {
      const follow = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
      logBox.textContent = state.log.text;
      if (follow) logBox.scrollTop = logBox.scrollHeight;
    }

    const key = `${latest.id}:${latest.state}:${(latest.videos || []).length}`;
    if (key !== resultKey) {
      resultKey = key;
      resultHost.replaceChildren();
      if (latest.state === "done" && latest.kind === "test") resultHost.append(testResult(latest));
      else if (latest.state === "done" && latest.kind === "batch") resultHost.append(batchResult(latest));
      else if (failed) resultHost.append(failureBlock(latest));
      if (failed) { detailsOpen = true; syncDetails(); }
    }
    tickElapsed();
  }

  function runLabel(latest) {
    const base = KIND_TITLES[latest.kind] || "Job";
    if (latest.kind === "preview") return latest.meta?.tier === "draft" ? "Draft clip" : "Storyboard";
    if (latest.kind === "render") return `Final render · ${plural(latest.meta?.videos || 0, "video")}`;
    if (latest.kind === "plan") return latest.meta?.test ? "Planning a test video" : `Planning ${plural(latest.meta?.videos || 0, "video")}`;
    return base;
  }

  function testResult(latest) {
    const video = latest.videos?.[0];
    if (!video) {
      return h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "The run finished but no video was found in the test folder." }),
        h("p", { text: "Open Show details to see what happened." })));
    }
    return h("div", { class: "result" },
      h("video", { src: video.url, controls: true, preload: "metadata", playsinline: true, "aria-label": `Preview: ${video.title}` }),
      h("div", { class: "meta" },
        h("h3", { text: video.title }),
        h("dl", {}, h("dt", { text: "Size" }), h("dd", { text: formatBytes(video.size) }),
          h("dt", { text: "Saved to" }), h("dd", { class: "mono", text: video.path })),
        h("p", { class: "help", text: "This is a test. It was not uploaded and sits in a separate folder, so tomorrow's real run is unaffected." })));
  }

  function batchResult(latest) {
    return h("div", { class: "notice good" }, icon("check"), h("div", { class: "notice-body" },
      h("p", { text: "The daily batch finished." }),
      h("p", {}, "Your videos are listed below and in ", h("a", { class: "link", href: "#/videos", text: "Videos" }), ".")));
  }

  function failureBlock(latest) {
    const lines = (latest.error_tail || []).filter(Boolean);
    const raw = lines[lines.length - 1] || "";
    const friendly = explain(raw);
    return h("div", { class: "notice bad", role: "alert" }, icon("alert"), h("div", { class: "notice-body" },
      h("p", {}, h("strong", { text: friendly || "The job stopped before it finished." })),
      raw && friendly !== raw ? h("p", { class: "mono", text: raw }) : null,
      h("p", { class: "help", text: "Open Show details for the full log. Common causes: no internet, Voicebox not running, or a missing API key." })));
  }

  function explain(line) {
    if (/voicebox/i.test(line)) return "Couldn't reach Voicebox. Open the Voicebox app, wait a few seconds, and try again.";
    if (/No stories scraped/i.test(line)) return "No stories were found in today's newsletters yet. Try again later.";
    if (/api[_ ]?key|authentication|401/i.test(line)) return "The AI writer rejected the API key. Check ANTHROPIC_API_KEY in your .env file.";
    if (/snapshot wrote|no video/i.test(line)) return "The preview renderer didn't produce frames. Try again; if it keeps failing, see the log.";
    return line;
  }

  function tickElapsed() {
    const latest = state.runs.latest;
    if (!latest || run.hidden) return;
    const end = latest.finished_at || Date.now() / 1000;
    runElapsed.textContent = formatDuration(end - latest.created_at);
  }
  const timer = setInterval(tickElapsed, 1000);
  onCleanup(() => clearInterval(timer));

  // ---- plans ----
  async function refreshPlans(preferRef) {
    try {
      const data = await loadPlans();
      const refs = data.plans.map((p) => p.ref);
      const latest = state.runs.latest;
      const fromJob = latest?.meta?.ref && refs.includes(latest.meta.ref) ? latest.meta.ref : null;
      const ref = preferRef && refs.includes(preferRef) ? preferRef
        : state.planRef && refs.includes(state.planRef) ? state.planRef
          : fromJob || (refs.includes(todayIso()) ? todayIso() : refs[0] || null);
      await loadPlan(ref);
    } catch { /* shown on the next poll */ }
  }

  // ---- today's videos ----
  async function refreshToday() {
    try {
      const data = await api.get("/api/videos");
      state.videos = data;
      const key = JSON.stringify(data.today);
      if (key !== todayKey) { todayKey = key; renderToday(data.today); }
    } catch { /* keep the previous list */ }
  }

  function renderToday(day) {
    todayTitle.textContent = `Rendered today, ${formatDay(day.date)}`;
    const total = day.videos.length;
    todayMeta.textContent = total ? `${plural(total, "video")}` : "";
    todayHost.replaceChildren();
    if (!total) {
      const sched = state.runs.schedule;
      todayHost.append(h("div", { class: "panel empty" },
        h("h3", { text: isRunning() ? "Videos will appear here as they finish" : "No videos rendered yet today" }),
        h("p", { text: isRunning() ? "A job is in progress. This list updates by itself."
          : sched?.time ? `The scheduled run starts at ${formatClock(sched.time)}. Or plan today's videos above and render the ones you like.`
            : "Plan today's videos above, then render the ones you like." })));
      return;
    }
    todayHost.append(h("ul", { class: "panel vlist" }, day.videos.map((v) => videoRow(v))));
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
          v.has_file ? h("span", { text: formatBytes(v.size) }) : null,
          v.youtube ? h("span", { text: v.youtube.privacy ? `On YouTube, ${v.youtube.privacy}` : "On YouTube" }) : null),
        v.status.error ? h("div", { class: "errline", text: v.status.error }) : null),
      h("div", { class: "acts" }, watch,
        v.youtube ? h("a", { class: "btn small inline", href: v.youtube.url, target: "_blank", rel: "noopener noreferrer" }, "Open on YouTube", icon("link", "sm")) : null),
      player);
    return li;
  }

  // ---- sync + wiring ----
  function sync() {
    syncMake();
    syncRun();
  }
  sync();
  todayHost.replaceChildren(h("div", { class: "panel empty" }, h("div", { class: "skeleton", "aria-hidden": "true" }), h("p", { class: "help", text: "Loading today's videos" })));
  refreshToday();
  refreshPlans();
  const todayTimer = setInterval(refreshToday, isRunning() ? 8000 : 20000);
  onCleanup(() => clearInterval(todayTimer));
  let lastRunKey = "";
  onCleanup(subscribe(() => {
    sync();
    const latest = state.runs.latest;
    const key = latest ? `${latest.id}:${latest.state}` : "";
    if (key !== lastRunKey) {
      const changed = lastRunKey !== "";
      lastRunKey = key;
      refreshToday();
      if (changed && latest && ["plan", "rewrite", "preview", "render"].includes(latest.kind)) refreshPlans(latest.meta?.ref);
    }
  }));

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function approx(n) {
  const minutes = n * 8;
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours > 1 ? "s" : ""}${minutes % 60 ? ` ${minutes % 60} minutes` : ""}`;
}

export { formatDate };
