// Lineup: the standing description of the day's videos that the automatic run reads.
//   how many videos a day  ->  batches (count + format + colours)  ->  the slots they make  ->  save.
// There is no list of planned videos here on purpose: the run is automatic. To see how a format
// looks, "Preview" makes a free draft of it on a sample story; the Results at the bottom say which
// format each uploaded video was, so formats can be compared over time.
import { api, ApiError } from "../api.js";
import { h, icon, spinner, plural, nextId } from "../dom.js";
import { toast, costTag, ago } from "../components.js";
import { state, subscribe, isRunning, presets, presetInfo, notify } from "../store.js";
import { stylePicker, previewTile, describeValue } from "../stylepicker.js";

const FAMILY = { paper: "Clean paper", glass: "Dark glass", avatar: "Avatar" };
const FAMILY_SHORT = { paper: "Paper", glass: "Glass", avatar: "Avatar" };
const clone = (x) => JSON.parse(JSON.stringify(x));
const pick = (list) => list[Math.floor(Math.random() * list.length)];

function shuffled(list) {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; }
  return out;
}

export function mountLineup(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let info = null; // GET /api/lineup
  let doc = null; // the lineup being edited: {order, batches[], slots{}}
  let savedJson = "";
  let once = false;
  let undo = null; // {doc, note}: what Randomize / a quick start replaced
  let note = ""; // what the last quick action chose
  let open = -1; // the batch whose format editor is open
  let selected = null; // the slot whose override editor is open
  let expanded = { ok: true, slots: [] };
  let expandTimer = null;
  let saving = false;
  let tryState = null; // {batch, job, ref, video, draft?, error?}
  let results = null;
  const pickers = [];

  // ---- layout ----
  const nextRun = h("section", { class: "panel next-run", "aria-live": "polite", "aria-label": "Next run" });
  const head = h("header", { class: "page-head" }, h("div", {}, h("h1", { text: "Lineup" }),
    h("p", { class: "lede", text: "What your automatic run makes every day: how many videos, and the format of each. Set it once; it runs by itself, so there is no list of planned videos to review." })));
  const totalHost = h("div", { class: "lineup-total" });
  const quickHost = h("div", { class: "quick", role: "group", "aria-label": "Quick start" });
  const noteHost = h("div", { class: "quick-note", "aria-live": "polite" });
  const batchHost = h("div", { class: "batches" });
  const slotHost = h("div");
  const footer = h("div", { class: "lineup-foot", role: "group", "aria-label": "Save" });
  const resultsHost = h("section", { class: "section", "aria-labelledby": "results-h" });
  root.append(head, nextRun,
    h("section", { class: "section", "aria-labelledby": "setup-h" },
      h("div", { class: "section-head" }, h("h2", { id: "setup-h", text: "Videos per day and formats" }),
        h("p", { text: "Split the day into batches. Each batch is a number of videos in one format. For example 3 clean paper in one colour and 4 dark glass in alternating colours." })),
      totalHost, quickHost, noteHost, batchHost),
    h("section", { class: "section", "aria-labelledby": "slots-h" }, h("div", { class: "section-head" }, h("h2", { id: "slots-h", text: "The day, slot by slot" })), slotHost),
    footer, resultsHost);

  // ---- model helpers ----
  const total = () => doc.batches.reduce((a, b) => a + b.count, 0);
  const dirty = () => JSON.stringify(doc) !== savedJson;
  const maxTotal = () => info?.max_total || 20;
  const swatches = (id, key) => presetInfo(id)?.colors?.[key] || [];
  const colourName = (preset, hex) => (swatches(preset, "accent_swatches").find((s) => s.hex === hex)?.name || hex || "its own colour").toLowerCase();

  function newBatch(kind, count, opts = {}) {
    if (kind === "avatar") return { name: "", count, style: "avatar", preset: "", bg: "", accent: "", accent_mode: "one", palette: [], character: opts.character ?? (state.characters.find((c) => c.is_selected)?.slug || state.characters.find((c) => c.slug)?.slug || ""), background: opts.background || "gameplay" };
    const p = presetInfo(kind === "glass" ? "cinematic-glass" : "paper");
    const palette = kind === "glass" ? [...(info?.rotation_default || [])] : (p?.colors?.accent_swatches || []).slice(0, 5).map((s) => s.hex);
    return { name: "", count, style: "explainer", preset: p?.id || "paper", bg: "", accent: opts.accent ?? "", accent_mode: opts.mode || "one", palette: opts.mode && opts.mode !== "one" ? palette : [], character: "" };
  }

  function batchValue(b) {
    return { style: b.style, preset: b.preset || "paper", bg: b.bg || "", accent: b.accent || "", character: b.character || "", background: b.background === "none" ? "none" : "gameplay", mode: b.accent_mode || "one", rotate: [...(b.palette || [])] };
  }
  function applyValue(b, v) {
    b.style = v.style; b.preset = v.style === "avatar" ? "" : v.preset; b.bg = v.style === "avatar" ? "" : v.bg; b.character = v.style === "avatar" ? v.character : ""; b.background = v.style === "avatar" ? (v.background === "none" ? "none" : "gameplay") : "";
    b.accent = v.style === "avatar" ? "" : v.accent; b.accent_mode = v.style === "avatar" ? "one" : v.mode || "one"; b.palette = v.style === "avatar" ? [] : [...(v.rotate || [])];
  }
  const familyOf = (b) => (b.style === "avatar" ? "avatar" : b.preset === "cinematic-glass" ? "glass" : "paper");
  function colourText(b) {
    if (b.style === "avatar") return `${b.character ? (state.characters.find((c) => c.slug === b.character)?.name || b.character) : "default avatar"}, ${b.background === "none" ? "regular" : "Minecraft"} background`;
    if (b.accent_mode === "alternate") return "alternating colours";
    if (b.accent_mode === "random") return "random colours";
    return b.accent ? `${colourName(b.preset, b.accent)}` : "its own colour";
  }
  const batchTitle = (b) => `${FAMILY[familyOf(b)]}, ${colourText(b)}`;

  /** Changing the total keeps the batches adding up to it: the last batch takes the difference. */
  function setTotal(t) {
    t = Math.max(1, Math.min(maxTotal(), t));
    let diff = t - total();
    if (!diff) return;
    const before = clone(doc.batches);
    while (diff !== 0 && doc.batches.length) {
      const last = doc.batches[doc.batches.length - 1];
      const next = last.count + diff;
      if (next >= 1) { last.count = next; diff = 0; } else { doc.batches.pop(); diff = next; } // not enough left: drop the batch, carry the rest
    }
    if (!doc.batches.length) doc.batches.push(newBatch("paper", t));
    const changed = doc.batches[doc.batches.length - 1];
    note = before.length !== doc.batches.length ? `The last batch was removed to make ${plural(t, "video")}.` : `Adjusted the last batch (${batchTitle(changed)}) to ${plural(changed.count, "video")} so the total is ${t}.`;
    doc.slots = {};
    changedDoc();
  }

  function changedDoc() {
    selected = null;
    render();
    clearTimeout(expandTimer);
    expandTimer = setTimeout(refreshExpansion, 200);
  }

  async function refreshExpansion() {
    try {
      expanded = await api.post("/api/lineup/expand", { lineup: doc });
    } catch (err) {
      expanded = { ok: false, errors: [err.message] };
    }
    renderSlots();
    renderFooter();
  }

  // ---- next run banner ----
  function renderNextRun() {
    if (!info) { nextRun.replaceChildren(h("div", { class: "skeleton", "aria-hidden": "true" })); return; }
    const nr = info.next_run;
    const when = new Date(`${nr.date}T${nr.time || "00:00"}`);
    const day = when.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
    const at = nr.time ? ` at ${when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "";
    const groups = new Map();
    for (const s of nr.slots) {
      const g = groups.get(s.batch) || { n: 0, s };
      g.n += 1; groups.set(s.batch, g);
    }
    const parts = [...groups.values()].map((g) => `${g.n} ${FAMILY_SHORT[g.s.family]} (${g.s.family === "avatar" ? `${g.s.look.character ? (state.characters.find((c) => c.slug === g.s.look.character)?.name || g.s.look.character) : "default avatar"}, ${g.s.look.background === "none" ? "regular" : "Minecraft"} background` : g.s.accent_mode === "alternate" ? "alternating" : g.s.accent_mode === "random" ? "random colours" : colourName(g.s.look.preset, g.s.look.accent)})`);
    nextRun.replaceChildren(
      h("span", { class: "next-icon", "aria-hidden": "true" }, icon("film")),
      h("div", {}, h("strong", { text: `Next run: ${day}${at}` }),
        h("p", { text: `Will make ${plural(nr.total, "video")}${parts.length ? `: ${parts.join(" · ")}` : ""}.` }),
        nr.uses === "once" ? h("p", { class: "once-note" }, h("span", { class: "chip warn", text: "One-off" }), " This lineup is for that run only; the standing lineup comes back after it. ",
          h("button", { class: "btn-link", type: "button", onClick: clearOnce }, "Cancel the one-off")) : null,
        info.saved ? null : h("p", { class: "faint small", text: "No lineup saved yet: the run uses your settings (the same look for every video). Save one below to take over." })));
  }

  async function clearOnce() {
    try { info = await api.put("/api/lineup", { clear_once: true }); toast("The one-off is cancelled."); renderNextRun(); } catch (err) { toast(err.message, "bad"); }
  }

  // ---- total + quick start ----
  function stepper({ value, min, max, onChange, label }) {
    const input = h("input", { class: "input stepper-input", type: "number", min, max, value, inputmode: "numeric", "aria-label": label,
      onChange: () => { const n = parseInt(input.value, 10); if (Number.isFinite(n)) onChange(Math.max(min, Math.min(max, n))); else input.value = value; } });
    return h("div", { class: "stepper", role: "group", "aria-label": label },
      h("button", { class: "btn small", type: "button", "aria-label": `Fewer: ${label}`, disabled: value <= min, onClick: () => onChange(value - 1) }, icon("minus", "sm")),
      input,
      h("button", { class: "btn small", type: "button", "aria-label": `More: ${label}`, disabled: value >= max, onClick: () => onChange(value + 1) }, icon("plus", "sm")));
  }

  function renderTotal() {
    const t = total();
    const time = info?.next_run?.time;
    totalHost.replaceChildren(
      h("div", { class: "total-row" },
        h("span", { class: "label", text: "Videos per day" }), stepper({ value: t, min: 1, max: maxTotal(), label: "Videos per day", onChange: setTotal }),
        h("span", { class: "help", text: `1 to ${maxTotal()}. Runs automatically${time ? ` at ${timeLabel(time)}` : " every day"}.` })));
  }
  const timeLabel = (hhmm) => new Date(`2000-01-01T${hhmm}`).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  function quickStart(kind) {
    const t = total();
    undo = { doc: clone(doc), label: "that quick start" };
    if (kind === "glass") doc.batches = [newBatch("glass", t, { mode: "alternate" })];
    else if (kind === "paper") doc.batches = [newBatch("paper", t, { accent: presetInfo("paper")?.colors?.accent || "" })];
    else {
      const a = Math.ceil(t / 2);
      doc.batches = t === 1 ? [newBatch("paper", 1)] : [newBatch("paper", a), newBatch("glass", t - a, { mode: "alternate" })];
    }
    note = { glass: "All dark glass, alternating colours.", paper: "All clean paper, one colour.", half: "Half clean paper, half dark glass." }[kind];
    doc.slots = {};
    open = -1;
    changedDoc();
  }

  function randomize() {
    const t = total();
    undo = { doc: clone(doc), label: "the randomized lineup" };
    const paperN = t === 1 ? pick([0, 1]) : 1 + Math.floor(Math.random() * (t - 1)); // both formats whenever there is room
    const glassN = t - paperN;
    const batches = [];
    const paperSw = swatches("paper", "accent_swatches"), glassSw = swatches("cinematic-glass", "accent_swatches");
    const bgPaper = swatches("paper", "bg_swatches"), bgGlass = swatches("cinematic-glass", "bg_swatches");
    const colourise = (b, sw, bgs) => {
      const mode = pick(["one", "alternate", "random"]);
      b.accent_mode = mode;
      if (mode === "one") b.accent = pick(sw).hex;
      else b.palette = shuffled(sw.map((s) => s.hex)).slice(0, 3 + Math.floor(Math.random() * 3));
      if (Math.random() < 0.4) b.bg = pick(bgs).hex;
      return b;
    };
    if (paperN) batches.push(colourise(newBatch("paper", paperN), paperSw, bgPaper));
    if (glassN) batches.push(colourise(newBatch("glass", glassN), glassSw, bgGlass));
    doc.batches = batches;
    doc.slots = {};
    note = `Randomized: ${batches.map((b) => `${b.count} × ${batchTitle(b)}`).join(" + ")}.`;
    open = -1;
    changedDoc();
  }

  function shuffleColours() {
    undo = { doc: clone(doc), label: "the colour shuffle" };
    for (const b of doc.batches) {
      if (b.style === "avatar") continue;
      const sw = swatches(b.preset, "accent_swatches");
      if (b.accent_mode === "one") b.accent = pick(sw).hex;
      else b.palette = shuffled(b.palette.length >= 2 ? b.palette : sw.map((s) => s.hex));
    }
    note = "Shuffled the colours.";
    changedDoc();
  }

  function renderQuick() {
    const q = (label, fn, title) => h("button", { class: "btn small", type: "button", title, onClick: fn }, label);
    quickHost.replaceChildren(
      h("span", { class: "label small", text: "Quick start" }),
      q("All dark glass, alternating", () => quickStart("glass")), q("All clean paper", () => quickStart("paper")), q("50/50 paper and glass", () => quickStart("half")),
      h("span", { class: "grow" }),
      q([icon("shuffle", "sm"), "Randomize"], randomize, "A random split between paper and glass with random colours. You can undo it."),
      q("Shuffle colours", shuffleColours, "New random colours for the batches you have"));
    noteHost.replaceChildren();
    if (note) {
      noteHost.append(h("div", { class: "notice good" }, icon("info"), h("div", { class: "notice-body" }, h("p", { text: note }),
        undo ? h("button", { class: "btn-link", type: "button", onClick: () => { doc = undo.doc; undo = null; note = "Undone."; open = -1; changedDoc(); } }, "Undo") : null)));
    }
  }

  // ---- batches ----
  function renderBatches() {
    pickers.splice(0).forEach((p) => p.dispose());
    batchHost.replaceChildren(...doc.batches.map((b, i) => batchCard(b, i)),
      doc.batches.length < (info?.max_batches || 10) ? h("button", { class: "btn add-batch", type: "button", onClick: addBatch }, icon("plus", "sm"), "Add batch") : null);
  }

  function addBatch() {
    const t = total();
    if (t >= maxTotal()) { toast(`The day is full (${maxTotal()} videos). Lower another batch first.`, "warn"); return; }
    doc.batches.push(newBatch("glass", 1, { mode: "alternate" }));
    open = doc.batches.length - 1;
    doc.slots = {};
    note = "";
    changedDoc();
  }

  function setCount(i, n) {
    const others = total() - doc.batches[i].count;
    n = Math.max(1, Math.min(maxTotal() - others, n));
    doc.batches[i].count = n;
    doc.slots = {};
    changedDoc();
  }

  function moveBatch(i, d) {
    const j = i + d;
    if (j < 0 || j >= doc.batches.length) return;
    [doc.batches[i], doc.batches[j]] = [doc.batches[j], doc.batches[i]];
    open = open === i ? j : open === j ? i : open;
    doc.slots = {};
    changedDoc();
  }

  function batchCard(b, i) {
    const isOpen = open === i;
    const v = batchValue(b);
    const fam = familyOf(b);
    const dot = b.style === "avatar" ? null : h("span", { class: "accent-dot", style: `--c:${b.accent_mode === "one" ? (b.accent || presetInfo(b.preset)?.colors?.accent || "#999") : (b.palette[0] || "#999")}` }, h("i", { "aria-hidden": "true" }));
    const card = h("article", { class: `batch${isOpen ? " open" : ""}`, "aria-label": `Batch ${i + 1}: ${batchTitle(b)}` });
    const tryBtn = h("button", { class: "btn small", type: "button", disabled: isRunning() || tryState?.job, onClick: () => tryFormat(i), title: "A free draft of this format on a sample story. Nothing is saved." },
      tryState?.batch === i && tryState.job ? spinner() : icon("play", "sm"), "Preview", costTag("free · 30–60 s"));
    card.append(h("div", { class: "batch-head" },
      h("span", { class: "batch-n", "aria-hidden": "true", text: String(i + 1) }),
      stepper({ value: b.count, min: 1, max: maxTotal() - (total() - b.count), label: `Videos in batch ${i + 1}`, onChange: (n) => setCount(i, n) }),
      h("span", { class: "times", "aria-hidden": "true", text: "×" }),
      previewTile({ ...v, accent: b.accent_mode !== "one" ? b.palette[0] : b.accent }, { small: true }),
      h("div", { class: "batch-title" }, h("strong", { text: FAMILY[fam] }), h("span", { class: "faint small" }, dot, " ", colourText(b))),
      h("div", { class: "batch-acts" },
        tryBtn,
        h("button", { class: "btn small quiet", type: "button", "aria-expanded": String(isOpen), onClick: () => { open = isOpen ? -1 : i; render(); } }, icon("sliders", "sm"), isOpen ? "Close" : "Edit format"),
        h("button", { class: "btn small quiet icon-btn", type: "button", "aria-label": `Move batch ${i + 1} up`, disabled: i === 0, onClick: () => moveBatch(i, -1) }, icon("arrow", "sm")),
        h("button", { class: "btn small quiet icon-btn down", type: "button", "aria-label": `Move batch ${i + 1} down`, disabled: i === doc.batches.length - 1, onClick: () => moveBatch(i, 1) }, icon("arrow", "sm")),
        h("button", { class: "btn small quiet", type: "button", disabled: total() + b.count > maxTotal() || doc.batches.length >= (info?.max_batches || 10), title: "Copy this batch", onClick: () => {
          doc.batches.splice(i + 1, 0, clone(b)); doc.slots = {}; open = i + 1; changedDoc();
        } }, icon("layers", "sm"), "Duplicate"),
        h("button", { class: "btn small quiet", type: "button", disabled: doc.batches.length < 2, onClick: () => { doc.batches.splice(i, 1); doc.slots = {}; open = -1; changedDoc(); } }, icon("x", "sm"), "Remove"))));
    if (isOpen) {
      const picker = stylePicker({ rotation: true, value: v, onChange: (nv) => { applyValue(b, nv); doc.slots = {}; changedDoc(); } });
      pickers.push(picker);
      card.append(h("div", { class: "batch-edit" }, picker));
    }
    if (tryState?.batch === i && (tryState.job || tryState.draft || tryState.error)) card.append(tryBlock());
    return card;
  }

  // ---- try a format: a free draft on a sample story ----
  async function tryFormat(i) {
    const slot = expanded.slots?.find((s) => s.batch === i + 1);
    if (!expanded.ok || !slot) { toast("Fix the lineup first: " + (expanded.errors?.[0] || "it has no slots"), "warn"); return; }
    tryState = { batch: i, job: null };
    try {
      const data = await api.post("/api/lineup/preview", { look: slot.look });
      tryState = { batch: i, job: data.job.id, ref: data.ref, video: data.video, started: Date.now() };
    } catch (err) {
      tryState = { batch: i, error: err instanceof ApiError ? err.message : "Couldn't start the preview." };
    }
    render();
  }

  function tryBlock() {
    const t = tryState;
    if (t.error) return h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" }, h("p", { text: t.error })));
    if (t.job) {
      const latest = state.runs.latest;
      return h("div", { class: "pv-progress", role: "status" }, spinner(), h("span", {}, h("strong", { text: "Rendering a free draft of this format (about 30–60 s)" }),
        h("span", { class: "faint", text: ` · ${latest?.id === t.job ? latest.stage || "starting" : "starting"}` })));
    }
    return h("figure", { class: "try-draft" },
      h("video", { src: `${t.draft.url}?t=${Math.round(t.draft.at || 0)}`, controls: true, preload: "metadata", playsinline: true, "aria-label": "Draft of this format" }),
      h("figcaption", {}, h("strong", { text: "Draft of this format" }), h("span", { class: "small faint", text: `on a sample story · ${Math.round(t.draft.seconds || 0)} s to render · silent, estimated timing · free` }),
        h("button", { class: "btn-link", type: "button", onClick: () => { tryState = null; render(); } }, "Hide")));
  }

  async function checkTry() {
    const latest = state.runs.latest;
    if (!tryState?.job || !latest || latest.id !== tryState.job || latest.state === "running") return;
    const t = tryState;
    try {
      const plan = await api.get(`/api/plans/${encodeURIComponent(t.ref)}`);
      const v = plan.videos.find((x) => x.id === t.video);
      if (latest.state === "done" && v?.preview?.draft) tryState = { batch: t.batch, draft: v.preview.draft };
      else tryState = { batch: t.batch, error: "The draft didn't finish. Open the Design lab's log, or try again." };
    } catch { tryState = { batch: t.batch, error: "Couldn't read the draft." }; }
    render();
  }

  // ---- slots ----
  function renderSlots() {
    slotHost.replaceChildren();
    const order = h("div", { class: "seg", role: "radiogroup", "aria-label": "Slot order" },
      [["interleave", "Interleave batches"], ["sequential", "In order"]].map(([val, label]) => h("label", {},
        h("input", { type: "radio", name: "slot-order", value: val, checked: doc.order === val, onChange: () => { doc.order = val; doc.slots = {}; changedDoc(); } }),
        h("span", { class: "opt", text: label }))));
    slotHost.append(h("div", { class: "slot-bar" }, order,
      h("span", { class: "help", text: doc.order === "interleave" ? "Formats are spread through the day (A, B, A, B…). Slot 1 posts first." : "Batch 1 first, then batch 2… Slot 1 posts first." })));
    if (!expanded.ok) {
      slotHost.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" }, ...(expanded.errors || ["The lineup isn't valid."]).map((e) => h("p", { text: e })))));
      return;
    }
    const grid = h("ol", { class: "slots", "aria-label": "Slots in publish order" }, expanded.slots.map((s) => {
      const on = selected === s.slot;
      const dotColour = s.look.style === "avatar" ? null : s.look.accent || presetInfo(s.look.preset)?.colors?.accent;
      return h("li", {}, h("button", { class: `slot${on ? " on" : ""}${s.overridden ? " over" : ""}`, type: "button", "aria-pressed": String(on), "aria-label": `Slot ${s.slot}: ${s.label}${s.overridden ? ", changed for this slot" : ""}. Click to change just this slot`,
        onClick: () => { selected = on ? null : s.slot; renderSlots(); } },
      h("span", { class: "slot-n", text: String(s.slot) }), previewTile({ ...s.look, preset: s.look.preset || "paper" }, { small: true }),
      h("span", { class: "slot-fmt" }, h("strong", { text: FAMILY_SHORT[s.family] }),
        dotColour ? h("span", { class: "accent-dot", style: `--c:${dotColour}` }, h("i", { "aria-hidden": "true" })) : h("span", { class: "faint small", text: s.look.character || "default" })),
      s.overridden ? h("span", { class: "chip active", text: "changed" }) : h("span", { class: "faint small", text: `batch ${s.batch}` })));
    }));
    slotHost.append(grid);
    if (selected) slotHost.append(slotEditor(expanded.slots.find((s) => s.slot === selected)));
  }

  function slotEditor(s) {
    if (!s) return h("div");
    const initial = { style: s.look.style, preset: s.look.preset || "paper", bg: s.look.bg || "", accent: s.look.accent || "", character: s.look.character || "", background: s.look.background === "none" ? "none" : "gameplay", mode: "one", rotate: [] };
    let value = initial;
    const picker = stylePicker({ value: initial, onChange: (nv) => { value = nv; } });
    pickers.push(picker);
    const use = h("button", { class: "btn primary", type: "button", onClick: () => {
      doc.slots[String(s.slot)] = value.style === "avatar" ? { style: "avatar", character: value.character, background: value.background === "none" ? "none" : "gameplay" }
        : { style: "explainer", preset: value.preset, bg: value.bg, accent: value.accent };
      selected = null; note = ""; changedDoc();
    } }, `Use this for slot ${s.slot}`);
    const reset = h("button", { class: "btn quiet", type: "button", disabled: !s.overridden, onClick: () => { delete doc.slots[String(s.slot)]; selected = null; changedDoc(); } }, "Reset to batch");
    return h("div", { class: "panel slot-edit" }, h("h3", { text: `Just slot ${s.slot}` }),
      h("p", { class: "help", text: `It belongs to batch ${s.batch}. Change its format or colours without touching the rest of the batch.` }),
      picker, h("div", { class: "form-actions" }, use, reset, h("button", { class: "btn quiet", type: "button", onClick: () => { selected = null; renderSlots(); } }, "Cancel")));
  }

  // ---- save ----
  function renderFooter() {
    const d = dirty();
    const t = total();
    const nextDay = info?.next_run?.date ? new Date(`${info.next_run.date}T00:00`).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }) : "the next run";
    const onceId = nextId("once");
    const check = h("input", { type: "checkbox", id: onceId, checked: once, onChange: () => { once = check.checked; renderFooter(); } });
    const save = h("button", { class: "btn primary", type: "button", disabled: !d && !once || saving || !expanded.ok, onClick: saveLineup, "aria-busy": saving ? "true" : "false" },
      saving ? spinner() : icon("check", "sm"), once ? "Save for the next run only" : "Save lineup");
    const undoBtn = h("button", { class: "btn quiet", type: "button", hidden: !d, onClick: () => { doc = JSON.parse(savedJson); undo = null; note = ""; open = -1; changedDoc(); } }, "Discard changes");
    footer.replaceChildren(
      h("div", { class: "lineup-foot-main" }, save, undoBtn,
        h("label", { class: "once", for: onceId }, check, h("span", {}, "Only for the next run, then go back to the previous lineup", h("span", { class: "help", text: ` (${nextDay})` })))),
      h("p", { class: `sp-state${d ? " dirty" : ""}`, role: "status", "aria-live": "polite", text: d
        ? `Not saved yet: ${plural(t, "video")} a day. ${once ? "Saving it applies to the next run only." : "Saving it uses it every day until you change it."}`
        : info?.saved ? `Saved: ${plural(t, "video")} a day, used every day until changed.` : "Nothing saved yet: the run uses your settings." }));
  }

  async function saveLineup() {
    if (saving) return;
    saving = true; renderFooter();
    try {
      info = await api.put("/api/lineup", { lineup: doc, once });
      if (!once) { adopt(info.lineup); } else { doc = JSON.parse(savedJson); }
      toast(once ? "Saved for the next run only. The standing lineup comes back after it." : "Lineup saved. The automatic run uses it from the next run.");
      once = false; undo = null; note = "";
      await Promise.all([api.get("/api/settings").then((s) => { state.settings = s; notify(); }).catch(() => {})]);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Couldn't save the lineup.", "bad", 9000);
    }
    saving = false;
    changedDoc();
  }

  function adopt(lineup) {
    doc = { order: lineup.order, batches: clone(lineup.batches), slots: clone(lineup.slots || {}) };
    savedJson = JSON.stringify(doc);
  }

  // ---- results ----
  async function loadResults() {
    try { results = await api.get("/api/results"); } catch { results = { groups: [], total: 0, days: 14, error: true }; }
    renderResults();
  }
  function renderResults() {
    resultsHost.replaceChildren(h("div", { class: "section-head" }, h("h2", { id: "results-h", text: "Results by format" }),
      h("p", { text: "The last 14 days of uploaded videos, grouped by the format they were made in, so you can see which format to keep. Views are not shown yet." })));
    if (!results) { resultsHost.append(h("div", { class: "skeleton", "aria-hidden": "true" })); return; }
    if (!results.groups.length) {
      resultsHost.append(h("div", { class: "panel empty" }, h("h3", { text: "Nothing uploaded in the last 14 days" }),
        h("p", { text: "Once videos are uploaded they appear here under the format they were made in." })));
      return;
    }
    resultsHost.append(h("div", { class: "results" }, results.groups.map((g) => h("details", { class: "panel result-group", open: results.groups.length <= 3 },
      h("summary", {}, h("strong", { text: g.format }), h("span", { class: "chip", text: plural(g.count, "video") }), h("span", { class: "faint small", text: "views: not tracked yet" })),
      h("ul", { class: "result-list" }, g.videos.map((v) => h("li", {},
        v.accent ? h("span", { class: "accent-dot", style: `--c:${v.accent}` }, h("i", { "aria-hidden": "true" })) : h("span", { class: "dot-empty" }),
        h("a", { class: "link", href: v.url, target: "_blank", rel: "noopener noreferrer", text: v.title }),
        h("span", { class: "faint small", text: `${v.date}${v.slot ? ` · slot ${v.slot}` : ""}${v.accent_mode ? ` · ${v.accent_mode}` : ""}` }))))))));
  }

  // ---- render all ----
  function render() {
    if (!doc) return;
    renderTotal(); renderQuick(); renderBatches(); renderSlots(); renderFooter(); renderNextRun();
  }

  async function load() {
    try {
      info = await api.get("/api/lineup");
      adopt(info.lineup);
      expanded = await api.post("/api/lineup/expand", { lineup: doc });
      render();
    } catch (err) {
      root.append(h("div", { class: "panel empty" }, h("h3", { text: "Couldn't load the lineup" }), h("p", { text: err.message })));
    }
    loadResults();
  }
  renderNextRun(); renderResults();
  load();
  onCleanup(subscribe(() => { if (tryState?.job) { checkTry(); renderBatches(); } }));
  onCleanup(() => { pickers.forEach((p) => p.dispose()); clearTimeout(expandTimer); });
  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}
