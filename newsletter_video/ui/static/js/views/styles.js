// The Styles page: reference videos you like, what was measured from them,
// the recipes they produced, and the defaults new videos draw on.
import { api, ApiError } from "../api.js";
import { h, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { savePill, toast } from "../components.js";
import { state, subscribe, value, setDraft } from "../store.js";
import { confirmDialog } from "./plan.js";

const MAX_VIDEO = 300 * 1024 * 1024;
const JOB_KEY = "bytesize.styleJob";
const remember = (id) => { try { id ? sessionStorage.setItem(JOB_KEY, id) : sessionStorage.removeItem(JOB_KEY); } catch { /* storage unavailable */ } };
const recall = () => { try { return sessionStorage.getItem(JOB_KEY); } catch { return null; } };

export function mountStyles(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let data = null;
  let query = "";
  let loadError = null;
  let addOpen = false;
  let pollTimer = null;
  let searchTimer = null;

  // ---- header ----
  const head = h("header", { class: "page-head" },
    h("div", {}, h("h1", { text: "Styles" }),
      h("p", { class: "lede", text: "Teach the explainer look by example. Drop in videos you like; their motion, pacing and palette become recipes the writer's planner draws on." })),
    savePill(onCleanup));

  // ---- defaults ----
  const onId = "style-lib-on";
  const onBox = h("input", { id: onId, type: "checkbox", role: "switch", onChange: () => setDraft("STYLE_LIBRARY", onBox.checked ? "style_library" : "off") });
  const onState = h("span", { class: "state" });
  const policySel = h("select", { class: "select", "aria-labelledby": "policy-l", onChange: () => setDraft("STYLE_RECIPE", policySel.value) });
  const policyHelp = h("p", { class: "help", id: "policy-help" });
  const defaults = h("section", { class: "section", "aria-labelledby": "defaults-h" },
    h("div", { class: "section-head" }, h("h2", { id: "defaults-h", text: "Defaults for new videos" }),
      h("p", { text: "What every video gets unless its own look says otherwise. Saved choices apply from the next plan, including the daily job." })),
    h("div", { class: "panel rows" },
      h("div", { class: "row" },
        h("div", {}, h("label", { class: "label", for: onId, text: "Use the style library" }),
          h("p", { class: "help", text: "Off means the template's own look, exactly as before the library existed." })),
        h("div", { class: "control" }, h("label", { class: "switch" }, onBox, h("span", { class: "track" }), onState))),
      h("div", { class: "row", id: "policy-row" },
        h("div", {}, h("span", { class: "label", id: "policy-l", text: "Style recipes" }), policyHelp),
        h("div", { class: "control" }, policySel))));

  // ---- library ----
  const search = h("input", { class: "input", type: "search", placeholder: "Search tags, notes, effects", "aria-label": "Search references",
    onInput: () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { query = search.value.trim(); load(); }, 250); } });
  const addButton = h("button", { class: "btn small primary", type: "button", onClick: () => { addOpen = !addOpen; renderAdd(); } }, icon("plus", "sm"), "Add a reference");
  const addHost = h("div");
  const listHost = h("div");
  const library = h("section", { class: "section", "aria-labelledby": "refs-h" },
    h("div", { class: "section-head split" }, h("div", {}, h("h2", { id: "refs-h", text: "References" }),
      h("p", { id: "refs-sub" })), h("div", { class: "form-actions" }, h("div", { class: "search-wrap" }, search), addButton)),
    addHost, listHost);

  root.append(head, defaults, library);

  // ---- add form ----
  const form = { file: null, busy: false };
  const nameInput = h("input", { class: "input", id: "ref-name", maxlength: 60, autocomplete: "off", placeholder: "For example, punchy type" });
  const notesInput = h("textarea", { class: "textarea", id: "ref-notes", maxlength: 600, rows: 2, placeholder: "What you like about it: big words, hard cuts, one accent colour" });
  const tagsInput = h("input", { class: "input", id: "ref-tags", maxlength: 200, placeholder: "favourite, hook" });
  const visionBox = h("input", { type: "checkbox", checked: true });
  const fileInput = h("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm", onChange: () => setFile(fileInput.files[0]) });
  const fileTitle = h("strong", { text: "Choose a video" });
  const fileSub = h("span", { class: "help", text: "MP4, MOV or WebM up to 300 MB. Drop a file here or click to browse. Only clips you have the right to study." });
  const drop = h("label", { class: "drop" }, fileInput, h("span", { class: "thumb" }, icon("film")), h("span", {}, fileTitle, fileSub));
  for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const type of ["dragleave", "drop"]) drop.addEventListener(type, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
  const fileError = h("p", { class: "error-text", hidden: true });
  const formError = h("div", { class: "notice bad", hidden: true, role: "alert" }, icon("alert"), h("div", { class: "notice-body" }));
  const progress = h("div", { class: "progress", hidden: true, role: "status" });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Analyse and add");
  const actions = h("div", { class: "form-actions full" }, submit,
    h("button", { class: "btn quiet", type: "button", onClick: () => { addOpen = false; renderAdd(); } }, "Cancel"),
    h("span", { class: "help", id: "cost-note" }));
  const addForm = h("form", { class: "panel addbox", novalidate: true, "aria-labelledby": "add-ref-h", onSubmit: onSubmit },
    h("div", { class: "addbox-head" },
      h("div", {}, h("h2", { id: "add-ref-h", text: "Add a reference" }),
        h("p", { class: "help", text: "The clip is analysed on this computer; only small frames go to the model, and nothing is ever committed or uploaded elsewhere." })),
      h("button", { class: "btn quiet small", type: "button", "aria-label": "Close", onClick: () => { addOpen = false; renderAdd(); } }, icon("x", "sm"))),
    h("div", { class: "form-grid" },
      h("div", { class: "field" }, h("label", { for: "ref-name", text: "Name" }), nameInput,
        h("p", { class: "help", text: "Short and memorable; it names the recipes too." })),
      h("div", { class: "field" }, h("span", { class: "label", text: "Video" }), drop, fileError),
      h("div", { class: "field full" }, h("label", { for: "ref-notes", text: "Notes (optional)" }), notesInput),
      h("div", { class: "field" }, h("label", { for: "ref-tags", text: "Tags (optional)" }), tagsInput,
        h("p", { class: "help", text: "Comma-separated. Searchable later." })),
      h("div", { class: "field" }, h("span", { class: "label", text: "Model description" }),
        h("label", { class: "switch" }, visionBox, h("span", { class: "track" }), h("span", { class: "state", text: "On" })),
        h("p", { class: "help", id: "vision-help" })),
      h("div", { class: "full stack" }, formError, progress, actions)));
  visionBox.addEventListener("change", syncVision);

  function syncVision() {
    const on = visionBox.checked;
    visionBox.nextSibling.nextSibling.textContent = on ? "On" : "Off";
    const hasKey = data?.keys?.ANTHROPIC_API_KEY;
    document.getElementById("vision-help").textContent = on
      ? (hasKey ? "Claude describes each scene in the explainer's vocabulary and refines the recreation. About 15 to 30 cents per reference."
        : "Needs ANTHROPIC_API_KEY. Without it the analysis is measurements only, which is free.")
      : "Measurements only (cuts, motion, palette, audio). Free, less descriptive recipes.";
    document.getElementById("cost-note").textContent = on && hasKey ? "Costs about 15 to 30 cents and takes a few minutes." : "Free. Takes a minute or two.";
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
    if (!name) { showError("Give the reference a name."); nameInput.focus(); return; }
    if (!form.file) { fileError.textContent = "Choose a video file."; fileError.hidden = false; fileInput.focus(); return; }
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
      poll(job.id);
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
  function setBusy(busy, label = "Analysing") {
    form.busy = busy;
    for (const control of addForm.querySelectorAll("input, textarea, button")) control.disabled = busy;
    progress.hidden = !busy;
    actions.hidden = busy;
    if (busy) {
      startedAt = startedAt || Date.now();
      const sub = h("span", { class: "help" });
      progress.replaceChildren(
        h("div", { class: "progress-head" }, spinner(), h("div", {}, h("strong", { text: label }), h("br"), sub)),
        h("div", { class: "bar", "aria-hidden": "true" }, h("i")),
        h("p", { class: "help", text: "Splitting into scenes, measuring motion, describing, recreating with the explainer composition and scoring the match. You can leave this page; it keeps going." }));
      progress._sub = sub;
    } else startedAt = 0;
  }

  async function poll(jobId) {
    clearTimeout(pollTimer);
    try {
      const res = await api.get(`/api/jobs/${jobId}`);
      const job = res.job;
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      const line = res.log.text.split("\n").map((s) => s.replace(/^\S+ \S+ \[\w+\] /, "").trim()).filter(Boolean).pop();
      if (progress._sub) progress._sub.textContent = `${formatDuration(seconds)} elapsed${line && !line.startsWith("Traceback") ? `. ${line.slice(0, 120)}` : ""}`;
      if (job.state === "running") { pollTimer = setTimeout(() => poll(jobId), 2000); return; }
      remember(null);
      setBusy(false);
      const outcome = res.outcome;
      if (job.state === "done" && outcome?.ok) {
        toast(`${outcome.name} added${outcome.fidelity != null ? `, fidelity ${outcome.fidelity.toFixed(2)}` : ""}${outcome.cost_usd != null ? `, about $${outcome.cost_usd.toFixed(2)}` : ""}.`);
        addOpen = false;
        nameInput.value = notesInput.value = tagsInput.value = "";
        form.file = null;
        fileTitle.textContent = "Choose a video";
        renderAdd();
        load();
        return;
      }
      showError(outcome?.error || (job.state === "interrupted" ? "The panel lost track of this job. Check whether the reference appears below." : "The analysis didn't finish. Check the terminal for details."));
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) { pollTimer = setTimeout(() => poll(jobId), 3000); return; }
      setBusy(false);
      showError(err.message);
    }
  }
  onCleanup(() => clearTimeout(pollTimer));

  function renderAdd() {
    addHost.replaceChildren();
    addButton.setAttribute("aria-expanded", String(addOpen));
    if (!addOpen) return;
    if (!data?.enabled) {
      addHost.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "The style library is switched off. Turn it on above and save, then add references." }))));
      return;
    }
    addHost.append(addForm);
    syncVision();
    nameInput.focus();
  }

  // ---- references ----
  async function remove(ref) {
    const ok = await confirmDialog({
      title: `Remove "${ref.title}"?`,
      body: h("p", { class: "muted", text: "Deletes the clip copy, its analysis and its recipes. Videos planned with these recipes keep rendering in the default look." }),
      confirmLabel: "Remove", tone: "danger",
    });
    if (!ok) return;
    try {
      await api.post(`/api/styles/${encodeURIComponent(ref.name)}/remove`, { confirm: true });
      toast(`${ref.title} removed.`);
      load();
    } catch (err) { toast(err.message, "bad"); }
  }

  function refCard(ref) {
    const detailsId = `ref-${ref.name}`;
    const details = h("div", { class: "ref-details", id: detailsId, hidden: true },
      ref.summary ? h("p", { class: "muted", text: ref.summary }) : null,
      h("div", { class: "recipes" }, ref.recipes.map((r) => h("div", { class: "recipe" },
        h("div", { class: "recipe-head" }, h("strong", { text: r.name }),
          r.fidelity != null ? h("span", { class: "faint small", text: `fidelity ${r.fidelity.toFixed(2)}` }) : null),
        h("p", { class: "small muted", text: r.description }),
        h("div", { class: "knobs" }, Object.entries(r.knobs || {}).map(([k, v]) => h("span", { class: "chip", text: `${k.replace("_", " ")} ${v}` })))))),
      ref.side_by_side ? h("figure", { class: "sb" }, h("img", { src: ref.side_by_side, alt: "Reference frames beside the recreation", loading: "lazy" }),
        h("figcaption", { class: "sb-cap" }, h("span", { text: "Reference (left) and the recreation with our composition (right)" }))) : null);
    const more = h("button", { class: "btn small quiet", type: "button", "aria-expanded": "false", "aria-controls": detailsId, onClick: () => {
      details.hidden = !details.hidden;
      more.setAttribute("aria-expanded", String(!details.hidden));
      more.replaceChildren(details.hidden ? "Recipes and details" : "Hide details");
    } }, "Recipes and details");
    return h("article", { class: "ref", "aria-label": ref.title },
      h("div", { class: "ref-main" },
        ref.contact_sheet ? h("img", { class: "ref-sheet", src: ref.contact_sheet, alt: `${ref.title} contact sheet`, loading: "lazy" })
          : h("div", { class: "ref-sheet nofile" }, "No contact sheet"),
        h("div", { class: "ref-body" },
          h("h3", { text: ref.title }),
          h("p", { class: "small muted", text: `${formatDuration(ref.duration)} · ${plural(ref.scene_count, "scene")} · ${plural(ref.recipes.length, "recipe")}` +
            (ref.fidelity != null ? ` · fidelity ${ref.fidelity.toFixed(2)}` : "") + (ref.cost_usd ? ` · cost $${ref.cost_usd.toFixed(2)}` : "") +
            (ref.vision_used ? "" : " · measurements only") }),
          ref.notes ? h("p", { class: "ref-notes", text: ref.notes }) : null,
          h("div", { class: "tags" }, [...ref.user_tags, ...ref.tags.filter((t) => !ref.user_tags.includes(t))].slice(0, 12).map((t) => h("span", { class: "chip", text: t }))),
          h("div", { class: "form-actions" }, more,
            h("button", { class: "btn small quiet danger-text", type: "button", onClick: () => remove(ref) }, icon("x", "sm"), "Remove")))),
      details);
  }

  function renderList() {
    listHost.replaceChildren();
    const sub = document.getElementById("refs-sub");
    if (loadError) {
      listHost.append(h("div", { class: "notice bad", role: "alert" }, icon("alert"), h("div", { class: "notice-body" }, h("p", { text: loadError }))));
      return;
    }
    if (!data) {
      listHost.append(h("div", { class: "ref-list" }, [1, 2].map(() => h("div", { class: "ref sk skeleton" }))));
      return;
    }
    if (!data.enabled) {
      sub.textContent = "";
      listHost.append(h("div", { class: "panel empty" }, h("h3", { text: "The style library is off" }),
        h("p", { text: "Turn it on above to add references and let the planner use them. Until then every video uses the template's own look." })));
      return;
    }
    sub.textContent = data.references.length ? `${plural(data.references.length, "reference")}${query ? ` matching "${query}"` : ""}, kept in ${data.root}/ (clips are never committed).` : "";
    if (!data.references.length) {
      listHost.append(h("div", { class: "panel empty" },
        h("h3", { text: query ? "No references match" : "No references yet" }),
        h("p", { text: query ? "Try other words: effects (wipe, typewriter), feel (bold, calm), or your own tags."
          : "Add a video whose motion you like. It is split into scenes, measured, recreated with the explainer composition and kept as recipes the planner can pick from." }),
        query ? null : h("button", { class: "btn primary", type: "button", onClick: () => { addOpen = true; renderAdd(); } }, icon("plus", "sm"), "Add a reference")));
      return;
    }
    listHost.append(h("div", { class: "ref-list" }, data.references.map(refCard)));
  }

  function syncDefaults() {
    const on = value("STYLE_LIBRARY") !== "off";
    onBox.checked = on;
    onState.textContent = on ? "On" : "Off";
    const policy = value("STYLE_RECIPE") || "auto";
    policySel.replaceChildren(
      h("option", { value: "auto", text: "Auto: the planner picks per scene" }),
      h("option", { value: "none", text: "None: the template's own look" }),
      ...(data?.references || []).flatMap((r) => r.recipes.map((rc) => h("option", { value: rc.id, text: `${r.title}: ${rc.name}` }))),
      ...(data?.builtin_recipes || []).map((rc) => h("option", { value: rc.id, text: `Built-in: ${rc.name}` })));
    if (![...policySel.options].some((o) => o.value === policy)) policySel.append(h("option", { value: policy, text: policy }));
    policySel.value = policy;
    policySel.disabled = !on;
    document.getElementById("policy-row").toggleAttribute("data-off", !on);
    policyHelp.textContent = !on ? "Only used when the library is on."
      : policy === "auto" ? "One small planner call per video (about 2 cents) chooses recipes from your references that fit each scene."
        : policy === "none" ? "No recipes unless a video's own look asks for one." : "Every video gets this one recipe on its middle scenes, with no planner call.";
  }

  async function load() {
    try {
      data = await api.get(`/api/styles${query ? `?q=${encodeURIComponent(query)}` : ""}`);
      loadError = null;
    } catch (err) { loadError = err.message; }
    renderList();
    syncDefaults();
    renderAdd();
  }

  syncDefaults();
  renderList();
  load();
  onCleanup(subscribe(syncDefaults));
  const resumeId = recall();
  if (resumeId) { addOpen = true; renderAdd(); setBusy(true); poll(resumeId); }

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}
