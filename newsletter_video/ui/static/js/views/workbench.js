// The Look workbench: one guided flow in three stages.
//   1. References: drop in videos you like and see how each was analysed.
//   2. Test video: write one throwaway video from today's newsletter, styled by a reference.
//   3. Tweak and compare: reference frames beside the storyboard, scene by scene;
//      every look change re-captures the storyboard for free. Rewrite and
//      final render are explicit, confirmed actions.
import { api, ApiError } from "../api.js";
import { h, append, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { toast, costTag, ago } from "../components.js";
import { state, subscribe, isRunning, runningJob, loadPlans, loadPlan, loadStyles, setBench, noteSpend, isTestRef,
  recipes as allRecipes, referenceForRecipe, referenceByName } from "../store.js";
import { planTest, startPreview, saveLook, openStudio, stopJob, rewriteVideo, renderFinal, removeReference, attempt, approxRender, WRITER_COST } from "../actions.js";
import { mountRunPanel } from "./run.js";
import { lookChips, lookLabel, recipeLabel, characterName, draftBlock, STYLE_LABEL, THEME_LABEL, BG_LABEL } from "./plan.js";

const MAX_VIDEO = 300 * 1024 * 1024;
const JOB_KEY = "bytesize.styleJob";
const HISTORY_KEY = "bytesize.looks";
const remember = (id) => { try { id ? sessionStorage.setItem(JOB_KEY, id) : sessionStorage.removeItem(JOB_KEY); } catch { /* storage unavailable */ } };
const recall = () => { try { return sessionStorage.getItem(JOB_KEY); } catch { return null; } };
const readHistory = () => { try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "{}") || {}; } catch { return {}; } };
const writeHistory = (hist) => { try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(hist)); } catch { /* storage unavailable */ } };

export function mountWorkbench(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let query = "";
  let searchTimer = null;
  let addOpen = false;
  let makeOpen = false;
  let busy = null; // "plan" | "look" | "stills" | "draft" | "voice" | "studio" | "rewrite" | "render" while a request is in flight
  let renderedKey = "";
  let pendingTestJob = null; // the plan job we started: its plan becomes the current test when it finishes
  const make = { recipe: null, character: undefined }; // choices for the next test video
  const history = readHistory(); // "<ref>/<vid>" -> [{at, label, frames, url}]

  // ---- header + rail ----
  const ledger = h("p", { class: "ledger", "aria-live": "polite" });
  const head = h("header", { class: "page-head" },
    h("div", {}, h("h1", { text: "Look workbench" }),
      h("p", { class: "lede", text: "Drop in a reference, write one test video in its style, then tweak the look beside it until it matches. Only Render makes a real video." })),
    ledger);
  const rail = h("ol", { class: "rail", "aria-label": "Where you are" });
  const next = h("p", { class: "rail-next" });
  const runHost = h("div");

  // ---- stage 1: references ----
  const search = h("input", { class: "input", type: "search", placeholder: "Search tags, notes, effects", "aria-label": "Search references",
    onInput: () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { query = search.value.trim(); load(); }, 250); } });
  const addButton = h("button", { class: "btn primary", type: "button", "aria-expanded": "false", onClick: () => { addOpen = !addOpen; renderAdd(); } }, icon("upload", "sm"), "Add a reference");
  const addHost = h("div");
  const stripHost = h("div");
  const detailHost = h("div");
  const refsSub = h("p", { class: "stage-sub" });
  const stage1 = stage(1, "References", refsSub, h("div", { class: "form-actions" }, h("div", { class: "search-wrap" }, search), addButton), addHost, stripHost, detailHost);

  // ---- stage 2: test video ----
  const testSub = h("p", { class: "stage-sub" });
  const switcher = h("div", { class: "plan-switch", role: "tablist", "aria-label": "Test videos" });
  const makeHost = h("div");
  const testHost = h("div");
  const stage2 = stage(2, "Test video", testSub, switcher, makeHost, testHost);

  // ---- stage 3: tweak and compare ----
  const tweakSub = h("p", { class: "stage-sub" });
  const compareHost = h("div");
  const stage3 = stage(3, "Tweak and compare", tweakSub, null, compareHost);

  root.append(head, rail, next, runHost, stage1, stage2, stage3);
  // jobs that belong to a test plan; once the plan list is known, a finished
  // job for a plan that no longer exists has nothing to show
  const runPanel = mountRunPanel(runHost, { accept: (job) => (Boolean(job.meta?.test) || isTestRef(job.meta?.ref))
    && (job.state === "running" || !state.plans || !job.meta?.ref || testCards().some((p) => p.ref === job.meta.ref)) });
  onCleanup(() => runPanel.dispose());

  function stage(n, title, sub, actions, ...body) {
    const id = `stage-${n}`;
    return h("section", { class: "stage", "aria-labelledby": id, dataset: { stage: String(n) } },
      h("div", { class: "stage-head" },
        h("div", {}, h("h2", { id }, h("span", { class: "stage-n", "aria-hidden": "true", text: String(n) }), title), sub),
        actions),
      ...body);
  }

  // ---- data helpers ----
  const refs = () => state.styles?.references || [];
  const testCards = () => (state.plans?.plans || []).filter((p) => isTestRef(p.ref));
  const plan = () => (state.plan && isTestRef(state.plan.ref) ? state.plan : null);
  const video = () => plan()?.videos?.[0] || null;
  const linkedRef = () => { const v = video(); return v ? referenceForRecipe(v.resolved.recipe) : null; };
  const selectedRef = () => referenceByName(state.bench.reference) || linkedRef() || refs()[0] || null;
  const jobFor = () => {
    const job = runningJob();
    const p = plan();
    return job && p && job.meta?.ref === p.ref ? job : null;
  };
  const blocked = () => isRunning() || Boolean(busy);
  const recipesOf = (ref) => (ref ? ref.recipes.map((r) => r.id) : []);
  const historyKey = () => `${plan().ref}/${video().id}`;

  // ---- actions ----
  async function selectRef(name) {
    setBench({ reference: name });
    make.recipe = null;
    renderedKey = "";
    render();
  }

  async function makeTest(recipeId) {
    if (blocked()) return;
    const ref = selectedRef();
    const recipe = recipeId ?? make.recipe ?? recipesOf(ref)[0] ?? "auto";
    const overrides = {};
    if (recipe && recipe !== "auto") { overrides.recipe = recipe; overrides.style = "explainer"; }
    if (make.character !== undefined && make.character !== "\u0000") overrides.character = make.character;
    busy = "plan";
    render();
    await attempt(async () => {
      await planTest(overrides);
      pendingTestJob = state.runs.latest?.id || null;
      makeOpen = false;
      runPanel.focus();
    });
    busy = null;
    renderedKey = "";
    render();
  }

  async function applyLook(patch) {
    if (blocked()) return;
    const p = plan(); const v = video();
    const look = { ...v.look };
    for (const [k, val] of Object.entries(patch)) { if (val === "" || val == null) delete look[k]; else look[k] = val; }
    rememberCurrentStills();
    busy = "look";
    render();
    await attempt(async () => {
      await saveLook(p.ref, v.id, look);
      await startPreview(p.ref, v.id, "stills");
    });
    busy = null;
    renderedKey = "";
    render();
  }

  function nextRecipe() {
    const ids = allRecipes().map((r) => r.id);
    if (!ids.length) return;
    const cur = video().resolved.recipe;
    const i = ids.indexOf(cur);
    applyLook({ recipe: ids[(i + 1) % ids.length], style: "explainer" });
  }

  async function preview(tier, voice = false) {
    if (blocked()) return;
    const p = plan(); const v = video();
    if (tier === "stills") rememberCurrentStills();
    busy = voice ? "voice" : tier;
    render();
    await attempt(() => startPreview(p.ref, v.id, tier, voice));
    busy = null;
    renderedKey = "";
    render();
  }

  async function studio() {
    if (busy) return;
    const p = plan(); const v = video();
    busy = "studio";
    render();
    await attempt(async () => { await openStudio(p.ref, v.id); await loadPlan(p.ref); });
    busy = null;
    renderedKey = "";
    render();
  }

  async function rewrite() {
    if (blocked()) return;
    busy = "rewrite";
    render();
    await attempt(async () => { if (await rewriteVideo(plan().ref, video())) runPanel.focus(); });
    busy = null;
    renderedKey = "";
    render();
  }

  async function renderNow() {
    if (blocked()) return;
    busy = "render";
    render();
    await attempt(async () => { if (await renderFinal(plan(), null)) runPanel.focus(); });
    busy = null;
    renderedKey = "";
    render();
  }

  // The last storyboard of this video, kept client-side so a new look can be
  // compared with the previous one while the next capture runs.
  function rememberCurrentStills() {
    const v = video();
    const s = v?.preview?.stills;
    if (!s) return;
    const key = historyKey();
    const list = history[key] || [];
    if (list.some((e) => e.at === s.at)) return;
    list.unshift({ at: s.at, label: lookLabel(v.resolved), url: `${s.url}?t=${Math.round(s.at || 0)}`,
      frames: (s.frames || []).map((f) => ({ url: f.url, label: f.label })) });
    history[key] = list.slice(0, 6);
    writeHistory(history);
  }

  // ---- stage 1 rendering ----
  function renderRefs() {
    const data = state.styles;
    stripHost.replaceChildren();
    detailHost.replaceChildren();
    if (!data) {
      stripHost.append(h("div", { class: "ref-strip" }, [1, 2, 3].map(() => h("div", { class: "ref-card sk skeleton" }))));
      return;
    }
    if (!data.enabled) {
      refsSub.textContent = "";
      stripHost.append(h("div", { class: "panel empty" }, h("h3", { text: "The style library is switched off" }),
        h("p", { text: "Turn it on in Settings to add references and style videos by example." }),
        h("a", { class: "btn primary", href: "#/settings" }, "Open Settings")));
      return;
    }
    const list = refs();
    refsSub.textContent = list.length
      ? `${plural(list.length, "reference")}${query ? ` matching "${query}"` : ""}. Pick one to style the test video by.`
      : "Videos whose motion, pacing and palette you like. Each becomes recipes the writer's planner can draw on.";
    if (!list.length) {
      stripHost.append(h("div", { class: "panel empty" },
        h("h3", { text: query ? "No references match" : "No references yet" }),
        h("p", { text: query ? "Try other words: effects (wipe, typewriter), feel (bold, calm), or your own tags."
          : "Drop in a short video you like. It is split into scenes, measured, recreated with the explainer composition and kept as recipes." }),
        query ? null : h("button", { class: "btn primary", type: "button", onClick: () => { addOpen = true; renderAdd(); } }, icon("upload", "sm"), "Add your first reference")));
      return;
    }
    const current = selectedRef();
    stripHost.append(h("div", { class: "ref-strip", role: "radiogroup", "aria-label": "References" }, list.map((ref) => refCard(ref, ref.name === current?.name))));
    if (current) detailHost.append(refDetail(current));
  }

  function refCard(ref, on) {
    const id = `ref-${ref.name}`;
    const input = h("input", { type: "radio", name: "reference", id, value: ref.name, checked: on, onChange: () => selectRef(ref.name) });
    const linked = linkedRef()?.name === ref.name;
    return h("label", { class: `ref-card${on ? " on" : ""}`, for: id }, input,
      ref.contact_sheet ? h("img", { class: "ref-thumb", src: ref.contact_sheet, alt: "", loading: "lazy" })
        : h("span", { class: "ref-thumb nofile" }, icon("film")),
      h("span", { class: "ref-card-body" },
        h("span", { class: "ref-name", text: ref.title }),
        h("span", { class: "ref-sub", text: `${formatDuration(ref.duration)} · ${plural(ref.scene_count, "scene")} · ${plural(ref.recipes.length, "recipe")}` }),
        h("span", { class: "ref-badges" },
          ref.fidelity != null ? h("span", { class: `chip ${ref.fidelity >= 0.7 ? "good" : ""}`, text: `match ${Math.round(ref.fidelity * 100)}%` }) : null,
          linked ? h("span", { class: "chip active", text: "On the test video" }) : null)),
      h("span", { class: "tick", "aria-hidden": "true" }, icon("check")));
  }

  function refDetail(ref) {
    const v = video();
    const applied = v?.resolved.recipe;
    const recipeRows = ref.recipes.map((r) => {
      const isOn = applied === r.id;
      const action = v
        ? h("button", { class: `btn small${isOn ? " quiet" : ""}`, type: "button", disabled: blocked() || isOn, onClick: () => applyLook({ recipe: r.id, style: "explainer" }) },
          isOn ? [icon("check", "sm"), "On the test video"] : [icon("wand", "sm"), "Try on the test video"])
        : h("button", { class: "btn small primary", type: "button", disabled: blocked(), onClick: () => makeTest(r.id) }, icon("wand", "sm"), "Make a test video with this");
      return h("div", { class: `recipe${isOn ? " on" : ""}` },
        h("div", { class: "recipe-head" }, h("strong", { text: r.name }),
          r.fidelity != null ? h("span", { class: "faint small tnum", text: `match ${Math.round(r.fidelity * 100)}%` }) : null),
        h("p", { class: "small muted", text: r.description }),
        h("div", { class: "knobs" }, Object.entries(r.knobs || {}).map(([k, val]) => h("span", { class: "chip", text: `${k.replace(/_/g, " ")} ${val}` }))),
        action);
    });
    const meta = [formatDuration(ref.duration), plural(ref.scene_count, "scene"), plural(ref.recipes.length, "recipe"),
      ref.vision_used ? "described by the model" : "measurements only", ref.cost_usd ? `cost $${ref.cost_usd.toFixed(2)}` : "free", ref.added ? `added ${ago(Date.parse(ref.added) / 1000)}` : ""].filter(Boolean);
    return h("article", { class: "ref-detail", "aria-label": `${ref.title}, analysis` },
      h("div", { class: "ref-sheet-col" },
        ref.contact_sheet ? h("a", { href: ref.contact_sheet, target: "_blank", rel: "noopener", "aria-label": "Open the contact sheet full size" },
          h("img", { class: "ref-sheet", src: ref.contact_sheet, alt: `${ref.title} contact sheet: one frame per scene` }))
          : h("div", { class: "ref-sheet nofile" }, "No contact sheet"),
        h("p", { class: "small faint", text: "Contact sheet: one frame per detected scene." }),
        ref.side_by_side ? h("figure", { class: "sb" }, h("img", { src: ref.side_by_side, alt: "Reference frames beside the recreation", loading: "lazy" }),
          h("figcaption", { class: "sb-cap" }, h("span", { text: "Reference (left) beside our composition's recreation (right)" }))) : null),
      h("div", { class: "ref-info" },
        h("h3", { text: ref.title }),
        h("p", { class: "small muted", text: meta.join(" · ") }),
        ref.notes ? h("p", { class: "ref-notes", text: ref.notes }) : null,
        ref.summary ? h("p", { class: "muted", text: ref.summary }) : null,
        h("div", { class: "tags" }, [...ref.user_tags, ...ref.tags.filter((t) => !ref.user_tags.includes(t))].slice(0, 12).map((t) => h("span", { class: "chip", text: t }))),
        h("h4", { text: "Recipes" }),
        h("div", { class: "recipe-list" }, recipeRows),
        h("div", { class: "form-actions" },
          h("button", { class: "btn small quiet danger-text", type: "button", onClick: () => attempt(async () => { if (await removeReference(ref)) { toast(`${ref.title} removed.`); if (state.bench.reference === ref.name) setBench({ reference: null }); load(); } }) },
            icon("x", "sm"), "Remove reference"))));
  }

  // ---- add form (analysis runs in the background; the page can be left) ----
  const form = { file: null, busy: false };
  const nameInput = h("input", { class: "input", id: "ref-name", maxlength: 60, autocomplete: "off", placeholder: "For example, punchy type" });
  const notesInput = h("textarea", { class: "textarea", id: "ref-notes", maxlength: 600, rows: 2, placeholder: "What you like about it: big words, hard cuts, one accent colour" });
  const tagsInput = h("input", { class: "input", id: "ref-tags", maxlength: 200, placeholder: "favourite, hook" });
  const visionBox = h("input", { type: "checkbox", checked: true });
  const visionState = h("span", { class: "state", text: "On" });
  const visionHelp = h("p", { class: "help" });
  const costNote = h("span", { class: "help" });
  const fileInput = h("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm", onChange: () => setFile(fileInput.files[0]) });
  const fileTitle = h("strong", { text: "Drop a video here, or click to choose" });
  const fileSub = h("span", { class: "help", text: "MP4, MOV or WebM up to 300 MB. Only clips you have the right to study." });
  const drop = h("label", { class: "drop" }, fileInput, h("span", { class: "thumb" }, icon("film")), h("span", {}, fileTitle, fileSub));
  for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const type of ["dragleave", "drop"]) drop.addEventListener(type, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
  const fileError = h("p", { class: "error-text", hidden: true });
  const formError = h("div", { class: "notice bad", hidden: true, role: "alert" }, icon("alert"), h("div", { class: "notice-body" }));
  const progress = h("div", { class: "progress", hidden: true, role: "status" });
  const submit = h("button", { class: "btn primary", type: "submit" }, icon("wand", "sm"), "Analyse and add");
  const actions = h("div", { class: "form-actions full" }, submit,
    h("button", { class: "btn quiet", type: "button", onClick: () => { addOpen = false; renderAdd(); } }, "Cancel"), costNote);
  const addForm = h("form", { class: "panel addbox", novalidate: true, "aria-labelledby": "add-ref-h", onSubmit: onSubmit },
    h("div", { class: "addbox-head" },
      h("div", {}, h("h3", { id: "add-ref-h", text: "Add a reference" }),
        h("p", { class: "help", text: "Analysed on this computer. Only small frames go to the model; the clip is never committed or uploaded elsewhere." })),
      h("button", { class: "btn quiet small", type: "button", "aria-label": "Close", onClick: () => { addOpen = false; renderAdd(); } }, icon("x", "sm"))),
    h("div", { class: "form-grid" },
      h("div", { class: "field full" }, h("span", { class: "label", text: "Video" }), drop, fileError),
      h("div", { class: "field" }, h("label", { for: "ref-name", text: "Name" }), nameInput, h("p", { class: "help", text: "Short and memorable; it names the recipes too." })),
      h("div", { class: "field" }, h("label", { for: "ref-tags", text: "Tags (optional)" }), tagsInput, h("p", { class: "help", text: "Comma-separated. Searchable later." })),
      h("div", { class: "field full" }, h("label", { for: "ref-notes", text: "Notes (optional)" }), notesInput),
      h("div", { class: "field full" }, h("span", { class: "label", text: "Model description" }),
        h("label", { class: "switch" }, visionBox, h("span", { class: "track" }), visionState), visionHelp),
      h("div", { class: "full stack" }, formError, progress, actions)));
  visionBox.addEventListener("change", syncVision);

  function syncVision() {
    const on = visionBox.checked;
    visionState.textContent = on ? "On" : "Off";
    const hasKey = state.styles?.keys?.ANTHROPIC_API_KEY;
    visionHelp.textContent = on
      ? (hasKey ? "Claude describes each scene in the explainer's vocabulary and refines the recreation. About 15 to 30 cents per reference."
        : "Needs ANTHROPIC_API_KEY. Without it the analysis is measurements only, which is free.")
      : "Measurements only (cuts, motion, palette, audio). Free, less descriptive recipes.";
    costNote.textContent = on && hasKey ? "About 15 to 30 cents, a few minutes." : "Free, a minute or two.";
  }

  function setFile(file) {
    fileError.hidden = true;
    form.file = null;
    if (!file) return;
    if (file.size > MAX_VIDEO) { fileError.textContent = `That file is ${formatBytes(file.size)}. The limit is ${formatBytes(MAX_VIDEO)}.`; fileError.hidden = false; return; }
    form.file = file;
    fileTitle.textContent = file.name;
    fileSub.textContent = `${formatBytes(file.size)}. Click to choose a different video.`;
    if (!nameInput.value.trim()) nameInput.value = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 60);
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (form.busy) return;
    formError.hidden = true;
    const name = nameInput.value.trim();
    if (!form.file) { fileError.textContent = "Choose a video file first."; fileError.hidden = false; fileInput.focus(); return; }
    if (!name) { showError("Give the reference a name."); nameInput.focus(); return; }
    const body = new FormData();
    body.set("name", name);
    body.set("notes", notesInput.value.trim());
    body.set("tags", tagsInput.value.trim());
    body.set("vision", visionBox.checked ? "1" : "0");
    body.set("video", form.file, "reference");
    setBusy(true, "Uploading");
    try {
      const { job } = await api.upload("/api/styles", body);
      remember(job.id);
      pollAdd(job.id);
    } catch (err) {
      setBusy(false);
      showError(err.message);
    }
  }

  function showError(message) {
    formError.hidden = false;
    formError.querySelector(".notice-body").replaceChildren(h("p", { text: message }));
  }

  let startedAt = 0;
  let pollTimer = null;
  function setBusy(on, label = "Analysing") {
    form.busy = on;
    for (const control of addForm.querySelectorAll("input, textarea, button")) control.disabled = on;
    progress.hidden = !on;
    actions.hidden = on;
    if (on) {
      startedAt = startedAt || Date.now();
      const sub = h("span", { class: "help" });
      progress.replaceChildren(
        h("div", { class: "progress-head" }, spinner(), h("div", {}, h("strong", { text: label }), h("br"), sub)),
        h("div", { class: "bar", "aria-hidden": "true" }, h("i")),
        h("p", { class: "help", text: "Splitting into scenes, measuring motion, describing, recreating with the explainer composition and scoring the match. You can leave this page; it keeps going." }));
      progress._sub = sub;
    } else startedAt = 0;
  }

  async function pollAdd(jobId) {
    clearTimeout(pollTimer);
    try {
      const res = await api.get(`/api/jobs/${jobId}`);
      const job = res.job;
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      const line = res.log.text.split("\n").map((s) => s.replace(/^\S+ \S+ \[\w+\] /, "").trim()).filter(Boolean).pop();
      if (progress._sub) progress._sub.textContent = `${formatDuration(seconds)} elapsed${line && !line.startsWith("Traceback") ? `. ${line.slice(0, 120)}` : ""}`;
      if (job.state === "running") { pollTimer = setTimeout(() => pollAdd(jobId), 2000); return; }
      remember(null);
      setBusy(false);
      const outcome = res.outcome;
      if (job.state === "done" && outcome?.ok) {
        noteSpend("references");
        toast(`${outcome.name} added${outcome.fidelity != null ? `, match ${Math.round(outcome.fidelity * 100)}%` : ""}${outcome.cost_usd != null ? `, about $${outcome.cost_usd.toFixed(2)}` : ""}.`);
        addOpen = false;
        nameInput.value = notesInput.value = tagsInput.value = "";
        form.file = null;
        fileTitle.textContent = "Drop a video here, or click to choose";
        setBench({ reference: outcome.name });
        renderAdd();
        load();
        return;
      }
      showError(outcome?.error || (job.state === "interrupted" ? "The panel lost track of this job. Check whether the reference appears below." : "The analysis didn't finish. Check the terminal for details."));
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) { pollTimer = setTimeout(() => pollAdd(jobId), 3000); return; }
      setBusy(false);
      showError(err.message);
    }
  }
  onCleanup(() => clearTimeout(pollTimer));

  function renderAdd() {
    addHost.replaceChildren();
    addButton.setAttribute("aria-expanded", String(addOpen));
    if (!addOpen) return;
    if (!state.styles?.enabled) {
      addHost.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "The style library is switched off. Turn it on in Settings, then add references." }))));
      return;
    }
    addHost.append(addForm);
    syncVision();
    nameInput.focus();
  }

  // ---- stage 2 rendering ----
  function renderTest() {
    const cards = testCards();
    const p = plan(); const v = video();
    const job = runningJob();
    const planning = job && job.kind === "plan" && job.meta?.test ? job : null;
    switcher.replaceChildren();
    if (cards.length > 1) {
      for (const card of cards.slice(0, 5)) {
        const on = state.planRef === card.ref;
        switcher.append(h("button", { class: `chip-btn${on ? " on" : ""}`, type: "button", role: "tab", "aria-selected": String(on),
          onClick: () => { setBench({ planRef: card.ref }); loadPlan(card.ref).then(() => { renderedKey = ""; render(); }).catch((e) => toast(e.message, "bad")); } },
        (card.titles?.[0] || "Test").slice(0, 32), h("span", { class: "faint", text: card.created ? ` · ${ago(Date.parse(card.created) / 1000)}` : "" })));
      }
    }
    testSub.textContent = v ? "A throwaway video from today's newsletter. Never uploaded, never counted as a covered story."
      : "One throwaway video written from today's newsletter, styled by the reference you picked. Never uploaded.";

    makeHost.replaceChildren();
    testHost.replaceChildren();
    if (planning) {
      testHost.append(h("div", { class: "pv-progress big", role: "status" }, spinner(),
        h("span", {}, h("strong", { text: "Writing the test video" }), h("span", { class: "faint", text: ` · ${planning.stage || "starting"}` }))));
      return;
    }
    if (!v || makeOpen) makeHost.append(makePanel(Boolean(v)));
    else makeHost.append(h("div", { class: "form-actions" },
      h("button", { class: "btn small", type: "button", disabled: blocked(), onClick: () => { makeOpen = true; renderedKey = ""; render(); } }, icon("plus", "sm"), "New test video…"),
      h("span", { class: "help" }, "Another writer call, ", costTag(WRITER_COST), ". The current test stays in the list.")));
    if (v) testHost.append(testCard(p, v));
  }

  function makePanel(cancelable) {
    const ref = selectedRef();
    const options = ref ? recipesOf(ref) : [];
    const chosen = make.recipe ?? options[0] ?? "auto";
    const recipeSel = h("select", { class: "select", id: "make-recipe", onChange: () => { make.recipe = recipeSel.value; } },
      ...(ref ? ref.recipes.map((r) => h("option", { value: r.id, text: `${r.name}${r.fidelity != null ? ` · match ${Math.round(r.fidelity * 100)}%` : ""}` })) : []),
      h("option", { value: "auto", text: "Planner picks recipes per scene" }),
      h("option", { value: "none", text: "No recipe: the template's own look" }));
    recipeSel.value = chosen;
    const charSel = h("select", { class: "select", id: "make-narrator", onChange: () => { make.character = charSel.value; } },
      h("option", { value: "\u0000", text: `Default (${characterName(state.plan?.default_look?.character ?? state.settings?.values?.CHARACTER)})` }),
      ...state.characters.map((c) => h("option", { value: c.slug, text: c.slug ? c.name : "Default narrator" })));
    charSel.value = make.character ?? "\u0000";
    const button = h("button", { class: "btn primary", type: "button", disabled: blocked(), onClick: () => makeTest(), "aria-busy": busy === "plan" ? "true" : "false" },
      busy === "plan" ? spinner() : icon("wand", "sm"), "Make a test video");
    return h("div", { class: "panel make-test" },
      h("div", { class: "make-test-row" },
        h("div", { class: "field" }, h("span", { class: "label", text: "Styled by" }),
          ref ? h("div", { class: "styled-by" }, ref.contact_sheet ? h("img", { src: ref.contact_sheet, alt: "" }) : icon("film"), h("strong", { text: ref.title }))
            : h("p", { class: "help", text: "No reference yet. Add one above, or make a test video with the template's own look." })),
        h("div", { class: "field" }, h("label", { for: "make-recipe", text: "Recipe" }), recipeSel),
        h("div", { class: "field" }, h("label", { for: "make-narrator", text: "Narrator" }), charSel)),
      h("div", { class: "make-test-foot" },
        button,
        cancelable ? h("button", { class: "btn quiet", type: "button", onClick: () => { makeOpen = false; renderedKey = ""; render(); } }, "Cancel") : null,
        h("span", { class: "help" }, "One writer call, ", costTag(WRITER_COST), " · about a minute · the storyboard comes next, free.")));
  }

  function testCard(p, v) {
    const ref = linkedRef();
    const status = v.status === "rendered" ? h("span", { class: "chip good" }, icon("check"), "Rendered at full quality")
      : v.status === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Render failed") : h("span", { class: "chip", text: "Written, not rendered" });
    const studioOn = p.studio && p.studio.ref === p.ref && p.studio.video === v.id ? p.studio : null;
    return h("article", { class: "tv", "aria-label": v.title },
      h("div", { class: "tv-head" },
        h("div", {}, h("h3", { class: "tv-title", text: v.title }),
          h("div", { class: "pv-meta" }, h("span", { class: "chip", text: v.badge }), status,
            h("span", { class: "faint small", text: `${v.scenes ? `${v.scenes.length} scenes, ` : ""}${v.words} words${p.created ? ` · written ${ago(Date.parse(p.created) / 1000)}` : ""}` }),
            v.writer === "heuristic" ? h("span", { class: "chip warn", text: "Rule-based script" }) : null)),
        h("div", { class: "tv-link" },
          ref ? [ref.contact_sheet ? h("img", { src: ref.contact_sheet, alt: "" }) : null,
            h("span", {}, h("span", { class: "faint small", text: "Styled by" }), h("strong", { text: ref.title }), h("span", { class: "small muted", text: recipeLabel(v.resolved.recipe) }))]
            : h("span", {}, h("span", { class: "faint small", text: "Styled by" }), h("strong", { text: recipeLabel(v.resolved.recipe) }), h("span", { class: "small muted", text: "No reference linked" })))),
      h("p", { class: "pv-stories small muted", text: v.stories.join(" · ") }),
      v.scenes ? h("ol", { class: "pv-scenes", "aria-label": "Scenes" }, v.scenes.map((t) => h("li", { text: t }))) : null,
      h("div", { class: "tv-actions" },
        h("button", { class: "btn small quiet", type: "button", disabled: blocked(), onClick: rewrite }, busy === "rewrite" ? spinner() : icon("refresh", "sm"), "Rewrite script…", costTag(WRITER_COST, "paid")),
        studioOn ? h("span", { class: "pv-studio" },
          h("a", { class: "btn small", href: studioOn.url, target: "_blank", rel: "noopener" }, icon("link", "sm"), "Studio open"),
          h("button", { class: "btn small quiet", type: "button", onClick: () => attempt(() => stopJob(studioOn.id).then(() => loadPlan(p.ref))), title: "Stops the Studio server" }, icon("stop", "sm"), "Stop"))
          : h("button", { class: "btn small quiet", type: "button", disabled: Boolean(busy), onClick: studio, title: "Opens the live composition in HyperFrames Studio (a new tab) to scrub it. Free." },
            busy === "studio" ? spinner() : icon("layers", "sm"), "Open in Studio", costTag("free"))));
  }

  // ---- stage 3 rendering ----
  function renderCompare() {
    const p = plan(); const v = video();
    compareHost.replaceChildren();
    if (!v) {
      tweakSub.textContent = "Reference frames beside your storyboard, scene by scene, with every look control next to it.";
      compareHost.append(h("div", { class: "panel empty" },
        h("h3", { text: "Make a test video first" }),
        h("p", { text: refs().length ? "Pick a reference above and press Make a test video. Its storyboard appears here beside the reference frames."
          : "Add a reference above, then make a test video. Its storyboard appears here beside the reference frames." })));
      return;
    }
    const ref = linkedRef() || selectedRef();
    const stills = v.preview?.stills;
    const job = jobFor();
    const capturing = (job && job.kind === "preview") || busy === "look" || busy === "stills" || busy === "draft" || busy === "voice";
    const prev = (history[historyKey()] || []).filter((e) => !stills || e.at !== stills.at);
    const fallback = !stills && capturing ? prev[0] : null;
    const rightFrames = stills ? (stills.frames || []) : fallback ? fallback.frames : [];
    const leftFrames = ref?.frames || [];
    const rows = Math.max(leftFrames.length, rightFrames.length, v.scenes?.length || 0, 1);
    tweakSub.textContent = stills
      ? `Storyboard ${ago(stills.at)}${stills.timing === "estimated timing" ? ", estimated timing" : ""}. Change anything below: the storyboard re-captures by itself (free, about 15 s).`
      : "Change anything below: the storyboard re-captures by itself (free, about 15 s).";

    // the side-by-side grid
    const grid = h("div", { class: "compare", role: "table", "aria-label": "Reference beside the test video, scene by scene" });
    grid.append(h("div", { class: "compare-head", role: "row" },
      h("div", { role: "columnheader" }, h("strong", { text: ref ? `Reference · ${ref.title}` : "Reference" }),
        h("span", { class: "small faint", text: ref ? (leftFrames.length ? `${plural(leftFrames.length, "scene")} · ${formatDuration(ref.duration)}` : "contact sheet") : "none linked" })),
      h("div", { role: "columnheader" }, h("strong", { text: stills ? "Test video · storyboard" : fallback ? "Test video · previous look" : "Test video" }),
        h("span", { class: "small faint", text: stills ? `${plural(rightFrames.length, "scene")} · about ${formatDuration(stills.duration || 0)} · ${stills.timing === "estimated timing" ? "estimated timing" : "real timing"}`
          : fallback ? fallback.label : "no storyboard yet" }))));
    if (!ref) {
      grid.append(h("div", { class: "compare-note" }, h("p", { class: "help", text: "This test video uses no reference recipe, so there is nothing to compare against. Pick a recipe below or a reference above." })));
    }
    for (let i = 0; i < rows; i++) {
      const left = leftFrames[i];
      const right = rightFrames[i];
      const label = right?.label || (v.scenes?.[i] ? `${i + 1} · ${v.scenes[i]}` : `Scene ${i + 1}`);
      grid.append(h("div", { class: "compare-row", role: "row" },
        h("div", { class: "cframe", role: "cell" }, left ? h("img", { src: left.url, alt: `Reference scene ${i + 1}` })
          : i === 0 && ref?.contact_sheet && !leftFrames.length ? h("img", { class: "sheet", src: ref.contact_sheet, alt: `${ref.title} contact sheet` })
            : h("span", { class: "frame-empty", text: ref ? "no scene here" : "" })),
        h("div", { class: "scene-label", role: "cell" }, h("span", { text: label }), left?.at != null ? h("span", { class: "faint tnum", text: `${left.at.toFixed(1)} s` }) : null),
        h("div", { class: `cframe${fallback ? " stale" : ""}`, role: "cell" }, right ? h("img", { src: right.url, alt: `Test video scene ${i + 1}` })
          : capturing ? h("span", { class: "frame-empty skeleton" }) : h("span", { class: "frame-empty", text: i === 0 ? "no storyboard yet" : "" }))));
    }
    const captureNote = capturing ? h("div", { class: "pv-progress", role: "status" }, spinner(),
      h("span", {}, h("strong", { text: job ? (job.meta?.tier === "draft" ? "Rendering a draft clip" : "Capturing the storyboard") : "Saving the look" }),
        h("span", { class: "faint", text: job ? ` · ${job.stage || "starting"}` : "" }))) : null;
    const openFull = stills ? h("p", { class: "small" }, h("a", { class: "link", href: stills.url, target: "_blank", rel: "noopener", text: "Open the full storyboard" }),
      h("span", { class: "faint", text: ` · ready in ${Math.round(stills.seconds || 0)} s` })) : null;

    append(compareHost, [grid, captureNote, openFull, tweakPanel(p, v), actionsRow(p, v)]);
    if (v.preview?.draft) compareHost.append(draftBlock(v.preview.draft));
    if (prev.length) {
      compareHost.append(h("div", { class: "earlier-looks" },
        h("h4", { text: "Earlier looks on this video" }),
        h("div", { class: "earlier-strip" }, prev.slice(0, 4).map((e) => h("figure", {},
          h("a", { href: e.url, target: "_blank", rel: "noopener" }, h("img", { src: e.url, alt: `Storyboard with ${e.label}`, loading: "lazy" })),
          h("figcaption", { class: "small" }, h("span", { text: e.label }), h("span", { class: "faint", text: ago(e.at) })))))));
    }
  }

  function tweakPanel(p, v) {
    const d = p.default_look;
    const off = blocked();
    const seg = (name, options, key) => h("div", { class: "seg", role: "radiogroup", "aria-label": name },
      options.map(([val, label]) => h("label", {},
        h("input", { type: "radio", name: `tweak-${key}`, value: val, checked: v.resolved[key] === val, disabled: off,
          onChange: () => applyLook({ [key]: val }) }),
        h("span", { class: "opt", text: label }))));
    const list = allRecipes();
    const recipeSel = h("select", { class: "select", id: "tweak-recipe", disabled: off, onChange: () => applyLook({ recipe: recipeSel.value, style: "explainer" }) },
      ...list.map((r) => { const src = referenceForRecipe(r.id); return h("option", { value: r.id, text: `${r.name}${src ? ` (${src.title})` : ""}${r.fidelity != null ? ` · match ${Math.round(r.fidelity * 100)}%` : ""}` }); }),
      h("option", { value: "auto", text: "Planner picks recipes per scene" }),
      h("option", { value: "none", text: "No recipe: the template's own look" }));
    recipeSel.value = v.resolved.recipe;
    const charSel = h("select", { class: "select", id: "tweak-narrator", disabled: off, onChange: () => applyLook({ character: charSel.value === "\u0000" ? "" : charSel.value }) },
      h("option", { value: "\u0000", text: `Default (${characterName(d.character)})` }),
      ...state.characters.map((c) => h("option", { value: c.slug, text: c.slug ? c.name : "Default narrator" })));
    charSel.value = v.look.character !== undefined ? v.look.character : "\u0000";
    const explainer = v.resolved.style === "explainer";
    const nextBtn = h("button", { class: "btn small", type: "button", disabled: off || list.length < 2, onClick: nextRecipe,
      title: list.length < 2 ? "Add another reference for more recipes to try" : "Switch to the next recipe and re-capture the storyboard" },
    busy === "look" ? spinner() : icon("shuffle", "sm"), "Try another recipe", costTag("free"));
    const field = (label, control, hint) => h("div", { class: `tweak-field${hint ? " hinted" : ""}` }, h("span", { class: "label", text: label }), control, hint ? h("span", { class: "help", text: hint }) : null);
    return h("div", { class: "tweak", "aria-label": "Look controls" },
      field("Recipe", h("div", { class: "tweak-recipe" }, recipeSel, nextBtn), explainer ? null : "Recipes apply to the Explainer style."),
      field("Style", seg("Style", [["explainer", "Explainer"], ["avatar", "Avatar"]], "style")),
      field("Narrator", charSel),
      explainer ? field("Theme", seg("Theme", [["light", "Light"], ["dark", "Dark"]], "theme")) : field("Background", seg("Background", [["gameplay", "Gameplay"], ["none", "Plain"]], "background")),
      h("p", { class: "help tweak-note", text: off && !busy ? "Controls unlock when the running job finishes." : "Each change is saved to this test video only and re-captures its storyboard. Your defaults and the daily videos are untouched." }));
  }

  function actionsRow(p, v) {
    const off = blocked();
    const btn = (key, label, iconName, cost, onClick, cls = "btn small") => h("button", { class: cls, type: "button", disabled: off, onClick },
      busy === key ? spinner() : icon(iconName, "sm"), label, cost);
    return h("div", { class: "tv-actions final-row" },
      btn("stills", "Storyboard again", "image", costTag("free · 15 s"), () => preview("stills")),
      btn("draft", "Draft clip", "play", costTag("free · 30 s"), () => preview("draft")),
      btn("voice", "Draft with voice", "voice", costTag("voice · 1 min"), () => preview("draft", true)),
      h("span", { class: "grow" }),
      h("button", { class: "btn small primary", type: "button", disabled: off || v.status === "rendered", onClick: renderNow,
        title: v.status === "rendered" ? "Already rendered" : "" },
      busy === "render" ? spinner() : icon("film", "sm"), "Render final…", costTag(`voiced · ${approxRender(1)}`, "paid")));
  }

  // ---- rail ----
  function renderRail() {
    const list = refs();
    const v = video();
    const stills = v?.preview?.stills;
    const ref = selectedRef();
    const current = !list.length ? 1 : !v ? 2 : 3;
    const items = [
      ["Reference", list.length ? `${ref?.title || ""}${list.length > 1 ? ` · ${list.length} in the library` : ""}` : "none yet"],
      ["Test video", v ? `${v.title.slice(0, 40)}${v.title.length > 40 ? "…" : ""}` : "none yet"],
      ["Tweak and compare", stills ? `storyboard ${ago(stills.at)}` : v ? "no storyboard yet" : "waiting for a test video"],
    ];
    rail.replaceChildren(...items.map(([label, sub], i) => h("li", { dataset: { s: i + 1 < current ? "done" : i + 1 === current ? "current" : "todo" }, "aria-current": i + 1 === current ? "step" : null },
      h("span", { class: "rail-n", "aria-hidden": "true" }, i + 1 < current ? icon("check") : String(i + 1)),
      h("span", { class: "rail-text" }, h("strong", { text: label }), h("span", { class: "rail-sub", text: sub })))));
    const job = runningJob();
    let hint;
    if (job) hint = ["Running: ", h("strong", { text: job.stage || "starting" })];
    else if (!list.length) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: () => { addOpen = true; renderAdd(); addForm.scrollIntoView({ block: "nearest", behavior: "smooth" }); } }, "add a reference video")];
    else if (!v) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: () => makeTest() }, "make a test video"), " styled by ", h("strong", { text: ref?.title || "" }), " (one writer call, ", costTag(WRITER_COST), ")"];
    else if (!stills) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: () => preview("stills") }, "capture the storyboard"), " (free, about 15 s)"];
    else hint = ["Next: compare, change the look until it matches, then ", h("strong", { text: "Render final" }), " when you are happy."];
    next.replaceChildren(...hint);
    const l = state.ledger;
    const cents = l.writer * 3;
    ledger.replaceChildren(h("strong", { text: "This session" }),
      h("span", { text: `${plural(l.writer, "writer call")} (about ${cents < 100 ? `${cents}¢` : `$${(cents / 100).toFixed(2)}`}) · ${plural(l.previews, "free preview")} · ${plural(l.renders, "render")}` }));
  }

  // ---- render all ----
  function render() {
    const job = runningJob();
    const key = JSON.stringify([state.styles, state.plans?.plans, state.plan, state.bench, busy, job?.id, job?.state, job?.stage, state.characters.length, isRunning(), makeOpen, state.ledger]);
    if (key === renderedKey) return;
    renderedKey = key;
    renderRail();
    renderRefs();
    renderTest();
    renderCompare();
  }

  // ---- loading ----
  async function load() {
    try { await loadStyles(query); } catch (err) { toast(err.message, "bad"); }
    render();
  }

  async function refreshPlans(preferRef) {
    try {
      const data = await loadPlans();
      const refs = data.plans.filter((p) => isTestRef(p.ref)).map((p) => p.ref);
      const want = preferRef && refs.includes(preferRef) ? preferRef
        : state.bench.planRef && refs.includes(state.bench.planRef) ? state.bench.planRef : refs[0] || null;
      if (want !== state.bench.planRef) setBench({ planRef: want });
      if (!want) { if (state.plan && isTestRef(state.plan.ref)) await loadPlan(null); }
      else if (state.planRef !== want || !state.plan) await loadPlan(want);
      else await loadPlan(want);
      rememberCurrentStills();
    } catch { /* shown on the next poll */ }
    render();
  }

  render();
  load();
  refreshPlans();
  let lastRunKey = "";
  onCleanup(subscribe(() => {
    render();
    const latest = state.runs.latest;
    const key = latest ? `${latest.id}:${latest.state}` : "";
    if (key !== lastRunKey) {
      const changed = lastRunKey !== "";
      lastRunKey = key;
      if (changed && latest && (latest.meta?.test || isTestRef(latest.meta?.ref))) {
        if (latest.kind === "plan" && latest.state !== "running" && latest.id === pendingTestJob) { pendingTestJob = null; refreshPlans(latest.meta.ref); }
        else refreshPlans(latest.meta?.ref);
      }
    }
  }));
  const resumeId = recall();
  if (resumeId) { addOpen = true; renderAdd(); setBusy(true); pollAdd(resumeId); }

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}
