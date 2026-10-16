import { api, ApiError } from "../api.js";
import { h, icon, spinner, formatDuration, formatBytes, formatClock, formatDay, plural } from "../dom.js";
import { toast } from "../components.js";
import { state, subscribe, value, isDirty, refreshRuns, isRunning } from "../store.js";

const FINISHED_VISIBLE_MS = 6 * 60 * 60 * 1000;
const DISMISS_KEY = "bytesize.dismissedRun";
const readDismissed = () => { try { return localStorage.getItem(DISMISS_KEY); } catch { return null; } };
const writeDismissed = (id) => { try { localStorage.setItem(DISMISS_KEY, id); } catch { /* storage unavailable */ } };

function confirmDialog({ title, body, confirmLabel, tone = "primary" }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { "aria-labelledby": "dlg-title" },
      h("form", { method: "dialog" },
        h("h2", { id: "dlg-title", text: title }), body,
        h("div", { class: "actions" },
          h("button", { class: "btn quiet", value: "cancel", type: "submit" }, "Cancel"),
          h("button", { class: `btn ${tone}`, value: "ok", type: "submit" }, confirmLabel))));
    dialog.addEventListener("close", () => { resolve(dialog.returnValue === "ok"); dialog.remove(); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

export function mountHome(root, ctx) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let todayKey = "";
  let resultKey = "";
  let startError = null;
  let starting = null; // "test" | "batch" while the request is in flight
  let detailsOpen = false;

  // ---- header ----
  const lede = h("p", { class: "lede" });
  const head = h("header", { class: "page-head" }, h("div", {}, h("h1", { text: "Studio" }), lede));

  // ---- make panel ----
  const testButton = h("button", { class: "btn primary", type: "button", onClick: startTest }, icon("play", "fill"), "Make a test video");
  const batchButton = h("button", { class: "btn", type: "button", onClick: startBatch }, "Run today's batch…");
  const summary = h("div", { class: "summary", "aria-label": "Settings used for the test video" });
  const unsavedNote = h("p", { class: "small faint", hidden: true, text: "Includes your unsaved changes." });
  const batchFacts = h("p", { class: "small faint" });
  const startErrorHost = h("div", { class: "section" });
  const make = h("section", { class: "panel make", "aria-label": "Start a run" },
    h("div", {}, h("h2", { text: "Test video" }),
      h("p", { text: "Makes one video with the choices below so you can check how it looks and sounds. It is never uploaded and never counts as a story you have covered." }),
      summary, h("span", { class: "spacer" }), testButton, unsavedNote),
    h("div", {}, h("h2", { text: "Today's batch" }),
      h("p", { text: "Runs the full daily job right now with your saved settings, exactly as the scheduled run would." }),
      batchFacts, h("span", { class: "spacer" }), batchButton));

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

  // ---- today ----
  const todayHost = h("div");
  const todayTitle = h("h2", { text: "Today" });
  const todayMeta = h("p", { class: "small faint" });
  const today = h("section", { class: "section", "aria-labelledby": "today-h" },
    h("div", { class: "today-head" }, todayTitle, todayMeta), todayHost);
  todayTitle.id = "today-h";

  root.append(head, make, startErrorHost, run, today);

  // ---- actions ----
  function draftOverrides() { return { ...state.draft }; }

  async function startTest() {
    if (starting || isRunning()) return;
    startError = null;
    starting = "test";
    sync();
    try {
      await api.post("/api/runs", { kind: "test", overrides: draftOverrides() });
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
      title: "Run today's batch now?",
      body: h("div", { class: "stack" },
        h("ul", {},
          h("li", { text: `Makes ${plural(n, "video")}. This can take a long while, about ${approx(n)}.` }),
          h("li", { text: uploads ? `Uploads them to YouTube as ${privacy}.` : "Doesn't upload them to YouTube." }),
          h("li", { text: "Uses your saved settings." })),
        isDirty() ? h("p", { class: "help", text: "You have unsaved changes. They are not included. Save them first if you want them used." }) : null,
        uploads ? h("p", { class: "help", text: "This is the real thing: it posts to your channel." }) : null),
      confirmLabel: "Run batch",
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
      title: "Stop this run?",
      body: h("p", { class: "muted", text: latest.kind === "test"
        ? "The test video won't be finished. Nothing else is affected."
        : "Videos already made are kept. Anything not yet made is skipped, and the next run will pick up from there." }),
      confirmLabel: "Stop run", tone: "danger",
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
    testButton.disabled = Boolean(busy);
    batchButton.disabled = Boolean(busy);
    testButton.setAttribute("aria-busy", starting === "test" ? "true" : "false");
    testButton.replaceChildren(starting === "test" ? spinner() : icon("play", "fill"), "Make a test video");
    batchButton.title = busy ? "A run is already in progress" : "";
    testButton.title = busy ? "A run is already in progress" : "";

    const style = value("VIDEO_STYLE");
    const chosen = state.characters.find((c) => c.slug === value("CHARACTER"));
    const chips = [h("span", { class: "chip", text: style === "avatar" ? "Avatar style" : "Explainer style" })];
    chips.push(h("span", { class: "chip", text: `${chosen ? chosen.name : "Default"} narrator` }));
    chips.push(h("span", { class: "chip", text: style === "avatar"
      ? (value("BACKGROUND_STYLE") === "gameplay" ? "Gameplay background" : "Plain background")
      : `${value("VIDEO_THEME") === "dark" ? "Dark" : "Light"} theme` }));
    summary.replaceChildren(...chips);
    unsavedNote.hidden = !isDirty();

    const s = state.settings?.values;
    if (s) {
      const n = Number(s.VIDEOS_PER_DAY);
      batchFacts.textContent = `${plural(n, "video")}, ${s.YOUTUBE_UPLOAD === "1" ? `uploaded to YouTube as ${s.YOUTUBE_PRIVACY}` : "not uploaded"}.`;
    }

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
      ? `Make a test video or run today's batch now. The daily job also runs by itself at ${formatClock(sched.time)}.`
      : "Make a test video or run today's batch now.";
  }

  function syncRun() {
    const latest = state.runs.latest;
    const visible = latest && (latest.state === "running" ||
      ((Date.now() / 1000 - (latest.finished_at || 0)) * 1000 < FINISHED_VISIBLE_MS && readDismissed() !== latest.id));
    run.hidden = !visible;
    if (!visible) return;

    const kindLabel = latest.kind === "test" ? "Test video" : "Today's batch";
    runTitle.textContent = kindLabel;
    const stateInfo = {
      running: ["active", "Running"], done: ["good", "Finished"], failed: ["bad", "Didn't finish"],
      cancelled: ["", "Stopped"], interrupted: ["warn", "Lost track"],
    }[latest.state] || ["", latest.state];
    runChip.className = `chip ${stateInfo[0]}`;
    runChip.replaceChildren(...[latest.state === "running" ? spinner() : latest.state === "done" ? icon("check") : null, stateInfo[1]].filter(Boolean));
    dismissButton.hidden = latest.state === "running";
    stopButton.hidden = latest.state !== "running";

    const uploads = latest.kind === "batch" && latest.meta?.uploads;
    const labels = [...STEP_LABELS.slice(0, 3), uploads ? "Upload" : "Done"];
    const failed = latest.state === "failed" || latest.state === "interrupted";
    const current = latest.state === "done" ? 5 : Math.max(1, latest.step || 1);
    steps.className = `steps${failed ? " failed" : ""}`;
    steps.replaceChildren(...labels.map((label, i) => h("li", {
      dataset: { s: i + 1 < current ? "done" : i + 1 === current ? "current" : "todo" },
      "aria-current": i + 1 === current && latest.state === "running" ? "step" : null,
    }, label)));

    if (latest.state === "running") {
      stageLine.textContent = latest.stage || "Starting up";
      lastLine.textContent = latest.last_line || "";
    } else if (latest.state === "done") {
      stageLine.textContent = latest.kind === "test" ? "Your test video is ready." : "The batch finished.";
      lastLine.textContent = "";
    } else if (latest.state === "cancelled") {
      stageLine.textContent = "You stopped this run.";
      lastLine.textContent = "";
    } else {
      stageLine.textContent = latest.state === "interrupted"
        ? "The panel lost track of this run, for example after a restart. It may have finished or been stopped."
        : "This run stopped with an error.";
      lastLine.textContent = "";
    }

    // log: only touch the DOM when the text changed; keep following the tail
    if (state.log.jobId === latest.id && logBox.textContent !== state.log.text) {
      const follow = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
      logBox.textContent = state.log.text;
      if (follow) logBox.scrollTop = logBox.scrollHeight;
    }

    // outcome block: rebuilt only when the run's state changes, so a playing video is never reset
    const key = `${latest.id}:${latest.state}:${(latest.videos || []).length}`;
    if (key !== resultKey) {
      resultKey = key;
      resultHost.replaceChildren();
      if (latest.state === "done" && latest.kind === "test") resultHost.append(testResult(latest));
      else if (latest.state === "done") resultHost.append(batchResult(latest));
      else if (failed) resultHost.append(failureBlock(latest));
      if (failed) { detailsOpen = true; syncDetails(); }
    }
    tickElapsed();
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
        h("p", { class: "help", text: "This is a test. It was not uploaded and sits in a separate folder, so tomorrow's real run is unaffected." }),
        h("div", { class: "run-actions" },
          h("a", { class: "btn small", href: "#/videos" }, "See all videos"),
          h("a", { class: "btn small quiet", href: "#/look" }, "Change the look"))));
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
      h("p", {}, h("strong", { text: friendly || "The run stopped before it finished." })),
      raw && friendly !== raw ? h("p", { class: "mono", text: raw }) : null,
      h("p", { class: "help", text: "Open Show details for the full log. Common causes: no internet, Voicebox not running, or a missing API key." })));
  }

  function explain(line) {
    if (/voicebox/i.test(line)) return "Couldn't reach Voicebox. Open the Voicebox app, wait a few seconds, and try again.";
    if (/No stories scraped/i.test(line)) return "No stories were found in today's newsletters yet. Try again later.";
    if (/api[_ ]?key|authentication|401/i.test(line)) return "The AI writer rejected the API key. Check ANTHROPIC_API_KEY in your .env file.";
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
    todayTitle.textContent = `Today, ${formatDay(day.date)}`;
    const total = day.videos.length;
    todayMeta.textContent = total ? `${plural(total, "video")}` : "";
    todayHost.replaceChildren();
    if (!total) {
      const sched = state.runs.schedule;
      todayHost.append(h("div", { class: "panel empty" },
        h("h3", { text: isRunning() ? "Videos will appear here as they finish" : "No videos yet today" }),
        h("p", { text: isRunning() ? "A run is in progress. This list updates by itself."
          : sched?.time ? `The scheduled run starts at ${formatClock(sched.time)}. Or make a test video above to see how things look.`
            : "Make a test video above to see how things look, or run today's batch." })));
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
    li.classList.add("withplayer");
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
  const todayTimer = setInterval(refreshToday, isRunning() ? 8000 : 20000);
  onCleanup(() => clearInterval(todayTimer));
  let lastRunKey = "";
  onCleanup(subscribe(() => {
    sync();
    const latest = state.runs.latest;
    const key = latest ? `${latest.id}:${latest.state}` : "";
    if (key !== lastRunKey) { lastRunKey = key; refreshToday(); }
  }));

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}

function approx(n) {
  const minutes = n * 8;
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours > 1 ? "s" : ""}${minutes % 60 ? ` ${minutes % 60} minutes` : ""}`;
}
