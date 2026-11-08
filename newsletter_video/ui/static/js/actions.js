// Everything that starts a job or writes to a plan, in one place, so the
// workbench and the daily page behave identically. Paid or real actions
// (a writer call, a final render, the daily job) always confirm first.
import { api } from "./api.js";
import { h, plural } from "./dom.js";
import { confirmDialog, toast } from "./components.js";
import { state, refreshRuns, isRunning, notify, noteSpend } from "./store.js";

export const planPath = (ref) => `/api/plans/${encodeURIComponent(ref)}`;
export const videoPath = (ref, vid) => `${planPath(ref)}/videos/${encodeURIComponent(vid)}`;
export const WRITER_COST = "about 3 cents";

export function approxRender(n, perVideo = 5) {
  const minutes = n * perVideo;
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours > 1 ? "s" : ""}${minutes % 60 ? ` ${minutes % 60} minutes` : ""}`;
}

/** Runs an action and turns its failure into a toast. Returns true on success. */
export async function attempt(fn) {
  try { await fn(); return true; } catch (err) { toast(err.message, "bad"); return false; }
}

// ---- free steps ----

export async function startPreview(ref, vid, tier = "stills", voice = false) {
  await api.post(`${videoPath(ref, vid)}/preview`, { tier, voice });
  noteSpend("previews");
  await refreshRuns();
}

export async function saveLook(ref, vid, look) {
  const data = await api.put(`${videoPath(ref, vid)}/look`, { look });
  if (state.planRef === ref) { state.plan = data; notify(); }
  return data;
}

// HyperFrames Studio: the live composition on a local port. The tab is
// opened on the click itself so the browser doesn't block it, then pointed
// at the server once it answers.
export async function openStudio(ref, vid) {
  const tab = window.open("about:blank", "_blank");
  try {
    const data = await api.post(`${videoPath(ref, vid)}/studio`, {});
    if (tab) tab.location = data.url; else window.open(data.url, "_blank");
    if (!data.ready) toast("Studio is still starting; reload its tab in a few seconds.", "warn");
  } catch (err) {
    if (tab) tab.close();
    throw err;
  }
}

export async function stopJob(id) {
  await api.post(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
  await refreshRuns();
}

// ---- paid or real steps: each confirms ----

export async function planTest(overrides) {
  await api.post("/api/plans", { ref: null, overrides });
  noteSpend("writer");
  await refreshRuns();
}

export async function planDay(iso, overrides, { replacing = false } = {}) {
  if (replacing) {
    const ok = await confirmDialog({
      title: "Plan today again?",
      body: h("p", { class: "muted", text: `Today already has a plan. Planning again replaces it with new story picks and scripts (one writer call, ${WRITER_COST}). Videos already rendered are kept.` }),
      confirmLabel: "Plan again",
    });
    if (!ok) return false;
  }
  await api.post("/api/plans", { ref: iso, overrides });
  noteSpend("writer");
  await refreshRuns();
  return true;
}

export async function rewriteVideo(ref, v) {
  const ok = await confirmDialog({
    title: "Rewrite this script?",
    body: h("div", { class: "stack" },
      h("p", { class: "muted", text: `The writer rewrites "${v.title}" from the same stories. The new script replaces this one; previews start over.` }),
      h("p", { class: "help", text: `A paid writer call, ${WRITER_COST}. Changing the look or previewing never costs anything.` })),
    confirmLabel: "Rewrite script",
  });
  if (!ok) return false;
  await api.post(`${videoPath(ref, v.id)}/rewrite`, { confirm: true });
  noteSpend("writer");
  await refreshRuns();
  return true;
}

export async function renderFinal(p, ids) {
  const s = state.settings.values;
  const uploads = s.YOUTUBE_UPLOAD === "1" && !p.is_test;
  const n = ids ? ids.length : p.videos.filter((v) => v.status !== "rendered").length;
  if (!n) { toast("Every selected video is already rendered.", "bad"); return false; }
  const ok = await confirmDialog({
    title: ids ? `Render ${plural(n, "video")} at full quality?` : "Render every planned video at full quality?",
    body: h("div", { class: "stack" },
      h("ul", {},
        h("li", { text: `Voices and renders ${plural(n, "video")} with the look set on each one. About ${approxRender(n)}.` }),
        h("li", { text: p.is_test ? "A test plan: never uploaded, never counts as a covered story."
          : uploads ? `Uploads them to YouTube as ${s.YOUTUBE_PRIVACY}, like the daily run.` : "Doesn't upload them (uploading is off in Settings)." }),
        h("li", { text: "Previews stay as they are. This is the only step that makes real videos." })),
      uploads ? h("p", { class: "help", text: "This is the real thing: it posts to your channel." }) : null),
    confirmLabel: ids ? "Render selected" : "Render all",
    tone: uploads ? "danger" : "primary",
  });
  if (!ok) return false;
  await api.post(`${planPath(p.ref)}/render`, { confirm: true, only: ids || null });
  noteSpend("renders");
  await refreshRuns();
  return true;
}

export async function runBatch() {
  const s = state.settings.values;
  const n = Number(s.VIDEOS_PER_DAY);
  const uploads = s.YOUTUBE_UPLOAD === "1";
  const ok = await confirmDialog({
    title: "Run the full daily job now?",
    body: h("div", { class: "stack" },
      h("p", { class: "muted", text: "This is the unattended 7:00 AM job: it plans, voices and renders everything in one go, with no review step." }),
      h("ul", {},
        h("li", { text: `Makes ${plural(n, "video")} at full quality. About ${approxRender(n, 8)}.` }),
        h("li", { text: uploads ? `Uploads them to YouTube as ${s.YOUTUBE_PRIVACY}.` : "Doesn't upload them to YouTube." }),
        h("li", { text: "Uses your saved settings and ignores any plan on this page." })),
      Object.keys(state.draft).length ? h("p", { class: "help", text: "You have unsaved changes. They are not included. Save them first if you want them used." }) : null,
      uploads ? h("p", { class: "help", text: "This is the real thing: it posts to your channel." }) : null),
    confirmLabel: "Run daily job",
    tone: uploads ? "danger" : "primary",
  });
  if (!ok) return false;
  await api.post("/api/runs", { kind: "batch", confirm: true });
  noteSpend("writer");
  noteSpend("renders");
  await refreshRuns();
  return true;
}

export async function stopRun(latest) {
  if (!latest || latest.state !== "running") return false;
  const ok = await confirmDialog({
    title: "Stop this job?",
    body: h("p", { class: "muted", text: latest.kind === "batch" || latest.kind === "render"
      ? "Videos already made are kept. Anything not yet made is skipped."
      : "It stops where it is. Nothing else is affected." }),
    confirmLabel: "Stop", tone: "danger",
  });
  if (!ok) return false;
  await stopJob(latest.id);
  return true;
}

export const busyNow = () => isRunning();
