import { api, ApiError } from "./api.js";

const listeners = new Set();

// ---- small persisted bits: what the workbench is looking at, what this session spent ----
const BENCH_KEY = "bytesize.bench";
const LEDGER_KEY = "bytesize.ledger";
const readJson = (store, key, fallback) => { try { return { ...fallback, ...(JSON.parse(store.getItem(key) || "null") || {}) }; } catch { return { ...fallback }; } };
const writeJson = (store, key, value) => { try { store.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } };

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
  plans: null, // {plans: [...], recipes: [...]}
  plan: null, // the plan being reviewed: {ref, kind, videos: [...]}
  planRef: null,
  styles: null, // the style library: {enabled, references, builtin_recipes, keys, root, adding}
  bench: readJson(localStorage, BENCH_KEY, { reference: null, planRef: null }),
  ledger: readJson(sessionStorage, LEDGER_KEY, { writer: 0, previews: 0, renders: 0, references: 0 }),
  saving: false,
  saveError: null,
  justSaved: false,
};

// ---- the look as the panel's settings describe it (for run overrides) ----
// A plan or preview started with unsaved look changes uses them as run
// overrides, so what you see on screen is what gets planned.
const LOOK_KEYS = { VIDEO_STYLE: "style", CHARACTER: "character", VIDEO_THEME: "theme", BACKGROUND_STYLE: "background", STYLE_RECIPE: "recipe" };
export function draftLook() {
  const out = {};
  for (const [env, field] of Object.entries(LOOK_KEYS)) if (env in state.draft) out[field] = state.draft[env];
  return out;
}

export const isTestRef = (ref) => Boolean(ref) && !/^\d{4}-\d{2}-\d{2}$/.test(ref);

export function setBench(patch) {
  state.bench = { ...state.bench, ...patch };
  writeJson(localStorage, BENCH_KEY, state.bench);
  notify();
}

// What this browser session has spent: writer calls are the only paid steps
// the workbench starts; previews are free but counted so the pace is visible.
export function noteSpend(kind) {
  state.ledger = { ...state.ledger, [kind]: (state.ledger[kind] || 0) + 1 };
  writeJson(sessionStorage, LEDGER_KEY, state.ledger);
  notify();
}

// ---- recipes and the references they came from ----
export const recipes = () => state.plans?.recipes || [];
export const recipeInfo = (id) => recipes().find((r) => r.id === id) || null;
export function referenceForRecipe(id) {
  if (!id || id === "auto" || id === "none") return null;
  return (state.styles?.references || []).find((r) => (r.recipes || []).some((x) => x.id === id)) || null;
}
export const referenceByName = (name) => (state.styles?.references || []).find((r) => r.name === name) || null;

export async function loadPlans() {
  state.plans = await api.get("/api/plans");
  notify();
  return state.plans;
}

export async function loadPlan(ref) {
  if (!ref) { state.plan = null; state.planRef = null; notify(); return null; }
  try {
    state.plan = await api.get(`/api/plans/${encodeURIComponent(ref)}`);
    state.planRef = ref;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) { state.plan = null; state.planRef = null; }
    else throw err;
  }
  notify();
  return state.plan;
}

export async function loadStyles(query = "") {
  state.styles = await api.get(`/api/styles${query ? `?q=${encodeURIComponent(query)}` : ""}`);
  notify();
  return state.styles;
}

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
export const runningJob = () => (state.runs.latest?.state === "running" ? state.runs.latest : null);
