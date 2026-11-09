// The run panel: the latest job's progress, its log and what to do when it
// fails. Both pages mount one; `accept(job)` says which jobs belong here.
import { h, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { toast } from "../components.js";
import { state, subscribe, isRunning } from "../store.js";
import { stopRun, attempt } from "../actions.js";

const FINISHED_VISIBLE_MS = 6 * 60 * 60 * 1000;
const DISMISS_KEY = "bytesize.dismissedRun";
const readDismissed = () => { try { return localStorage.getItem(DISMISS_KEY); } catch { return null; } };
const writeDismissed = (id) => { try { localStorage.setItem(DISMISS_KEY, id); } catch { /* storage unavailable */ } };

const STEP_LABELS = ["Newsletter", "Scripts", "Voice and video", "Done"];

export function runLabel(latest) {
  if (latest.kind === "preview") return latest.meta?.voice ? "Draft clip with voice" : latest.meta?.tier === "draft" ? "Draft clip" : "Storyboard";
  if (latest.kind === "render") return latest.meta?.test ? "Final render of the test video" : `Final render · ${plural(latest.meta?.videos || 0, "video")}`;
  if (latest.kind === "plan") return latest.meta?.test ? "Writing a test video" : `Planning ${plural(latest.meta?.videos || 0, "video")}`;
  return { rewrite: "Rewriting the script", batch: "Today's batch", test: "Test video" }[latest.kind] || "Job";
}

export function explainFailure(line) {
  if (/voicebox/i.test(line)) return "Couldn't reach Voicebox. Open the Voicebox app, wait a few seconds, and try again.";
  if (/No stories scraped/i.test(line)) return "No stories were found in today's newsletters yet. Try again later.";
  if (/api[_ ]?key|authentication|401/i.test(line)) return "The AI writer rejected the API key. Check ANTHROPIC_API_KEY in your .env file.";
  if (/snapshot wrote|no video/i.test(line)) return "The preview renderer didn't produce frames. Try again; if it keeps failing, see the log.";
  if (/nothing to plan|no valid structural plan|no stories selected/i.test(line)) return "The AI writer didn't run (it needs ANTHROPIC_API_KEY in the project's .env file), so no script could be written. Add the key, then try again.";
  return line;
}

// What a job is doing and roughly how long it takes, so a click is never silent.
function expectation(latest) {
  if (latest.kind === "plan") return latest.meta?.test ? "Reading today's newsletter and writing one script. Usually 20 to 40 seconds." : "Reading today's newsletter and writing the scripts. Usually under a minute.";
  if (latest.kind === "preview") return latest.meta?.tier === "draft" ? "Rendering a low-quality draft clip. Usually 30 to 60 seconds." : "Capturing one frame per scene. Usually about 15 seconds.";
  if (latest.kind === "rewrite") return "The writer is redoing the script. Usually 20 to 40 seconds.";
  return "";
}

export function mountRunPanel(host, { accept = () => true } = {}) {
  let resultKey = "";
  let detailsOpen = false;

  const title = h("h2", { tabindex: -1, id: "run-title" });
  const chip = h("span", { class: "chip" });
  const elapsed = h("span", { class: "small faint tnum" });
  const dismissButton = h("button", { class: "btn quiet small", type: "button", hidden: true, onClick: () => { if (state.runs.latest) writeDismissed(state.runs.latest.id); sync(); } }, "Dismiss");
  const steps = h("ol", { class: "steps", "aria-label": "Progress" });
  const stageLine = h("strong");
  const lastLine = h("span", { class: "last" });
  const stage = h("div", { class: "stage", "aria-live": "polite" }, stageLine, lastLine);
  const stopButton = h("button", { class: "btn small danger", type: "button", onClick: () => attempt(() => stopRun(state.runs.latest)) }, icon("stop", "sm"), "Stop");
  const detailsButton = h("button", { class: "btn small quiet", type: "button", "aria-expanded": "false", onClick: toggleDetails }, "Show log");
  const logBox = h("pre", { class: "log", hidden: true, tabindex: 0, "aria-label": "Run log" });
  const resultHost = h("div");
  const section = h("section", { class: "panel run", hidden: true, "aria-labelledby": "run-title" },
    h("div", { class: "run-head" }, title, chip, elapsed, detailsButton, dismissButton),
    h("div", { class: "run-body" }, steps, stage, resultHost, h("div", { class: "run-actions" }, stopButton), logBox));
  host.append(section);

  function toggleDetails() { detailsOpen = !detailsOpen; syncDetails(); }
  function syncDetails() {
    logBox.hidden = !detailsOpen;
    detailsButton.textContent = detailsOpen ? "Hide log" : "Show log";
    detailsButton.setAttribute("aria-expanded", String(detailsOpen));
    if (detailsOpen) logBox.scrollTop = logBox.scrollHeight;
  }

  function sync() {
    const latest = state.runs.latest;
    const visible = latest && accept(latest) && (latest.state === "running" ||
      ((Date.now() / 1000 - (latest.finished_at || 0)) * 1000 < FINISHED_VISIBLE_MS && readDismissed() !== latest.id));
    section.hidden = !visible;
    if (!visible) return;
    // a finished job folds to one line; failures and running jobs keep the full panel
    section.classList.toggle("slim", latest.state === "done" || latest.state === "cancelled");

    title.textContent = runLabel(latest);
    const info = {
      running: ["active", "Running"], done: ["good", "Finished"], failed: ["bad", "Didn't finish"],
      cancelled: ["", "Stopped"], interrupted: ["warn", "Lost track"],
    }[latest.state] || ["", latest.state];
    chip.className = `chip ${info[0]}`;
    chip.replaceChildren(...[latest.state === "running" ? spinner() : latest.state === "done" ? icon("check") : null, info[1]].filter(Boolean));
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
      lastLine.textContent = latest.last_line || expectation(latest);
    } else if (latest.state === "done") {
      stageLine.textContent = {
        plan: latest.meta?.test ? "The test video is written. Its storyboard is next." : "The plan is ready. Review it below.",
        rewrite: "The script was rewritten.", preview: "The preview is ready.",
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
      else if (latest.state === "done" && latest.kind === "batch") resultHost.append(batchResult());
      else if (failed) resultHost.append(failureBlock(latest));
      if (failed) { detailsOpen = true; syncDetails(); }
    }
    tick();
  }

  function tick() {
    const latest = state.runs.latest;
    if (!latest || section.hidden) return;
    const end = latest.finished_at || Date.now() / 1000;
    elapsed.textContent = formatDuration(end - latest.created_at);
  }

  const timer = setInterval(tick, 1000);
  const unsubscribe = subscribe(sync);
  sync();
  return {
    el: section,
    focus() { title.focus(); section.scrollIntoView({ block: "nearest", behavior: "smooth" }); },
    dispose() { clearInterval(timer); unsubscribe(); section.remove(); },
  };
}

function testResult(latest) {
  const video = latest.videos?.[0];
  if (!video) {
    return h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
      h("p", { text: "The run finished but no video was found in the test folder." }),
      h("p", { text: "Open the log to see what happened." })));
  }
  return h("div", { class: "result" },
    h("video", { src: video.url, controls: true, preload: "metadata", playsinline: true, "aria-label": `Preview: ${video.title}` }),
    h("div", { class: "meta" },
      h("h3", { text: video.title }),
      h("dl", {}, h("dt", { text: "Size" }), h("dd", { text: formatBytes(video.size) }),
        h("dt", { text: "Saved to" }), h("dd", { class: "mono", text: video.path })),
      h("p", { class: "help", text: "This is a test. It was not uploaded and sits in a separate folder, so tomorrow's real run is unaffected." })));
}

function batchResult() {
  return h("div", { class: "notice good" }, icon("check"), h("div", { class: "notice-body" },
    h("p", { text: "The daily batch finished. Your videos are listed below." })));
}

function failureBlock(latest) {
  const lines = (latest.error_tail || []).filter(Boolean);
  const raw = lines[lines.length - 1] || "";
  const friendly = explainFailure(raw);
  return h("div", { class: "notice bad", role: "alert" }, icon("alert"), h("div", { class: "notice-body" },
    h("p", {}, h("strong", { text: friendly || "The job stopped before it finished." })),
    raw && friendly !== raw ? h("p", { class: "mono", text: raw }) : null,
    h("p", { class: "help", text: "The full log is below. Common causes: no internet, Voicebox not running, or a missing API key." })));
}

export { toast, isRunning };
