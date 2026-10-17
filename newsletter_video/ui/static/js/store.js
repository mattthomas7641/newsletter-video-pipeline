import { api, ApiError } from "./api.js";

const listeners = new Set();

export const state = {
  loaded: false,
  loadError: null,
  settings: null, // {values, set, invalid, supported, choices, keys}
  draft: {}, // unsaved changes, key -> value
  characters: [],
  runs: { activity: [], latest: null, schedule: null },
  log: { jobId: null, text: "", next: 0 },
  setup: null,
  videos: null,
  saving: false,
  saveError: null,
  justSaved: false,
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify() {
  for (const fn of [...listeners]) fn(state);
}

// ---- settings draft ----

export const saved = (key) => state.settings?.values[key];
export const value = (key) => (key in state.draft ? state.draft[key] : saved(key));
export const dirtyKeys = () => Object.keys(state.draft);
export const isDirty = () => dirtyKeys().length > 0;

export function setDraft(key, next) {
  next = String(next);
  if (next === saved(key)) delete state.draft[key];
  else state.draft[key] = next;
  state.saveError = null;
  state.justSaved = false;
  notify();
}

export function discard() {
  state.draft = {};
  state.saveError = null;
  notify();
}

export async function save() {
  if (!isDirty() || state.saving) return false;
  state.saving = true;
  state.saveError = null;
  notify();
  try {
    const result = await api.put("/api/settings", { updates: { ...state.draft } });
    state.settings = result;
    state.draft = {};
    state.justSaved = true;
    loadSetup().catch(() => {}); // the checks depend on what is switched on
    return true;
  } catch (err) {
    state.saveError = err instanceof ApiError ? err.message : "Couldn't save your changes.";
    if (err instanceof ApiError && err.fields) {
      const first = Object.entries(err.fields)[0];
      if (first) state.saveError = `${first[0]}: ${first[1]}`;
    }
    return false;
  } finally {
    state.saving = false;
    notify();
  }
}

// ---- loading ----

export async function loadSettings() {
  state.settings = await api.get("/api/settings");
}

export async function loadCharacters() {
  state.characters = (await api.get("/api/characters")).characters;
  notify();
}

export async function loadVideos() {
  state.videos = await api.get("/api/videos");
  notify();
}

export async function loadSetup() {
  state.setup = await api.get("/api/setup");
  notify();
}

export async function loadAll() {
  try {
    await Promise.all([loadSettings(), loadCharacters(), refreshRuns()]);
    state.loaded = true;
    state.loadError = null;
  } catch (err) {
    state.loadError = err.message;
  }
  notify();
}

// ---- run polling ----

const LOG_CAP = 200_000;

export async function refreshRuns() {
  const data = await api.get("/api/runs");
  state.runs = data;
  const latest = data.latest;
  if (latest) {
    if (state.log.jobId !== latest.id) state.log = { jobId: latest.id, text: "", next: 0, final: false };
    if (latest.state === "running" || !state.log.final) {
      try {
        const chunk = await api.get(`/api/jobs/${latest.id}?since=${state.log.next}`);
        state.log.text = (state.log.text + chunk.log.text).slice(-LOG_CAP);
        state.log.next = chunk.log.next;
        if (chunk.job) state.runs.latest = chunk.job;
        if (chunk.job && chunk.job.state !== "running") state.log.final = true;
      } catch { /* the job directory may be mid-write; try again next tick */ }
    }
  }
  notify();
  return state.runs;
}

export const isRunning = () => state.runs.activity.length > 0;
