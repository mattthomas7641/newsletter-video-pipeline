import { api, ApiError } from "./api.js";
import { h, icon, spinner, nextId, formatDuration, formatBytes } from "./dom.js";
import { toast } from "./components.js";
import { loadCharacters, setDraft } from "./store.js";

const MAX_IMAGE = 8 * 1024 * 1024;
const MAX_CLIP = 25 * 1024 * 1024;
const MAX_CLIPS = 5;
const IDEAL = [5, 30];
const JOB_KEY = "bytesize.addJob";

const remember = (id) => { try { id ? sessionStorage.setItem(JOB_KEY, id) : sessionStorage.removeItem(JOB_KEY); } catch { /* storage unavailable */ } };
const recall = () => { try { return sessionStorage.getItem(JOB_KEY); } catch { return null; } };

/** Builds the "Add a character" panel. `onClose` is called when it should be hidden. */
export function addCharacterPanel({ onClose, onAdded }) {
  const ids = { name: nextId("name"), persona: nextId("persona"), profile: nextId("profile") };
  const form = { avatar: null, avatarInfo: null, clips: [], mode: "clone", profiles: null, profilesLoading: false, busy: false };
  let pollTimer = null;
  let startedAt = 0;

  const nameInput = h("input", { class: "input", id: ids.name, name: "name", maxlength: 40, autocomplete: "off", required: true,
    placeholder: "For example, Captain Zap", "aria-describedby": `${ids.name}-help` });
  const nameError = h("p", { class: "error-text", id: `${ids.name}-err`, hidden: true });
  const persona = h("textarea", { class: "textarea", id: ids.persona, name: "persona", maxlength: 400, rows: 3,
    placeholder: "Bubbly and endlessly upbeat, loves a pun" });

  // ---- avatar ----
  const avatarInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-describedby": "avatar-help",
    onChange: () => setAvatar(avatarInput.files[0]) });
  const avatarThumb = h("span", { class: "thumb" }, icon("upload"));
  const avatarTitle = h("strong", { text: "Choose an image" });
  const avatarSub = h("span", { class: "help", id: "avatar-help", text: "A PNG with a transparent background looks best. Drop a file here or click to browse." });
  const avatarDrop = h("label", { class: "drop" }, avatarInput, avatarThumb, h("span", {}, avatarTitle, avatarSub));
  const avatarWarn = h("div", { class: "notice warn", hidden: true, role: "status" }, icon("alert"), h("div", { class: "notice-body" }));
  const avatarError = h("p", { class: "error-text", hidden: true });
  for (const type of ["dragenter", "dragover"]) avatarDrop.addEventListener(type, (e) => { e.preventDefault(); avatarDrop.classList.add("over"); });
  for (const type of ["dragleave", "drop"]) avatarDrop.addEventListener(type, () => avatarDrop.classList.remove("over"));
  avatarDrop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) setAvatar(e.dataTransfer.files[0]); });

  async function setAvatar(file) {
    avatarError.hidden = true;
    avatarWarn.hidden = true;
    form.avatar = null;
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) return failAvatar("Use a PNG, JPEG or WebP image.");
    if (file.size > MAX_IMAGE) return failAvatar(`That image is ${formatBytes(file.size)}. The limit is ${formatBytes(MAX_IMAGE)}.`);
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      form.avatar = file;
      avatarThumb.replaceChildren(h("img", { src: url, alt: "Avatar preview" }));
      avatarTitle.textContent = file.name;
      avatarSub.textContent = `${img.naturalWidth} by ${img.naturalHeight} pixels, ${formatBytes(file.size)}. Click to choose a different image.`;
      const transparent = file.type !== "image/jpeg" && hasTransparency(img);
      if (!transparent) {
        avatarWarn.hidden = false;
        avatarWarn.querySelector(".notice-body").replaceChildren(
          h("p", {}, h("strong", { text: "This image has no transparent background." })),
          h("p", { text: "It will show up as a solid rectangle in the video. A cutout PNG with the background removed looks right. You can still continue." }));
      }
    };
    img.onerror = () => { URL.revokeObjectURL(url); failAvatar("That file couldn't be read as an image."); };
    img.src = url;
  }
  function failAvatar(message) {
    avatarError.textContent = message;
    avatarError.hidden = false;
    avatarThumb.replaceChildren(icon("upload"));
    avatarTitle.textContent = "Choose an image";
  }
  function hasTransparency(img) {
    const size = 128;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, size, size);
    const data = ctx.getImageData(0, 0, size, size).data;
    for (let i = 3; i < data.length; i += 4) if (data[i] < 250) return true;
    return false;
  }

  // ---- voice ----
  const modeOptions = [
    ["clone", "Clone from recordings"],
    ["profile", "Use a Voicebox voice"],
    ["default", "Standard voice"],
  ];
  const modeName = nextId("voice-mode");
  const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Voice source" },
    modeOptions.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name: modeName, value, checked: value === form.mode, onChange: () => setMode(value) }),
      h("span", { class: "opt", text: label }))));

  const clipInput = h("input", { type: "file", accept: "audio/*,.wav,.mp3,.m4a,.ogg,.flac", multiple: true,
    onChange: () => { addClips([...clipInput.files]); clipInput.value = ""; } });
  const clipList = h("ul", { class: "clips", "aria-label": "Voice clips" });
  const clipError = h("p", { class: "error-text", hidden: true });
  const clonePanel = h("div", { class: "stack" },
    h("div", { class: "notice guide" }, icon("info"), h("div", { class: "notice-body" },
      h("p", {}, h("strong", { text: "Good reference clips make a good clone." })),
      h("ul", {},
        h("li", { text: `Each clip ${IDEAL[0]} to ${IDEAL[1]} seconds long. Add more than one for a better match.` }),
        h("li", { text: "One person speaking, nothing else: no music, laughter, other voices or background noise." }),
        h("li", { text: "Natural speech without long pauses. WAV, MP3, M4A, OGG or FLAC." })))),
    h("div", { class: "form-actions" },
      h("label", { class: "btn small" }, icon("plus", "sm"), "Add voice clips", clipInput)),
    clipList, clipError);

  const profileSelect = h("select", { class: "select", id: ids.profile, onChange: () => { form.profileId = profileSelect.value; } });
  const profileStatus = h("div", { class: "stack" });
  const profilePanel = h("div", { class: "field" },
    h("label", { for: ids.profile, text: "Voicebox voice" }), profileSelect, profileStatus,
    h("p", { class: "help", text: "These are voices you already created in the Voicebox app." }));
  const defaultPanel = h("p", { class: "help", text: "This character will use the standard narrator voice from your settings. Pick this if you only want a new look." });

  function setMode(mode) {
    form.mode = mode;
    clonePanel.hidden = mode !== "clone";
    profilePanel.hidden = mode !== "profile";
    defaultPanel.hidden = mode !== "default";
    if (mode === "profile" && form.profiles === null) loadProfiles();
  }

  async function loadProfiles() {
    form.profilesLoading = true;
    renderProfiles();
    try { form.profiles = await api.get("/api/voicebox/profiles"); } catch (err) { form.profiles = { available: false, profiles: [], error: err.message }; }
    form.profilesLoading = false;
    renderProfiles();
  }
  function renderProfiles() {
    profileStatus.replaceChildren();
    profileSelect.replaceChildren();
    if (form.profilesLoading) {
      profileSelect.disabled = true;
      profileSelect.hidden = true;
      profileStatus.append(h("p", { class: "help" }, spinner(), " Looking for your Voicebox voices"));
      return;
    }
    const data = form.profiles;
    profileSelect.disabled = !data?.available || !data.profiles.length;
    profileSelect.hidden = profileSelect.disabled;
    if (!data?.available) {
      profileStatus.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "Voicebox isn't reachable." }),
        h("p", { text: "Open the Voicebox app, wait a few seconds, then try again." }),
        h("button", { class: "btn small", type: "button", onClick: loadProfiles }, icon("refresh", "sm"), "Try again"))));
    } else if (!data.profiles.length) {
      profileStatus.append(h("p", { class: "help", text: "No voices found. Create one in Voicebox, or clone from recordings instead." }));
    } else {
      profileSelect.append(h("option", { value: "", text: "Choose a voice" }), ...data.profiles.map((p) => h("option", { value: p.id, text: p.name })));
      profileSelect.value = form.profileId || "";
    }
  }

  async function addClips(files) {
    clipError.hidden = true;
    for (const file of files) {
      if (form.clips.length >= MAX_CLIPS) { showClipError(`Use at most ${MAX_CLIPS} clips.`); break; }
      if (file.size > MAX_CLIP) { showClipError(`${file.name} is ${formatBytes(file.size)}. Clips can be up to ${formatBytes(MAX_CLIP)}.`); continue; }
      const clip = { file, seconds: null };
      form.clips.push(clip);
      probe(clip);
    }
    renderClips();
  }
  function showClipError(message) { clipError.textContent = message; clipError.hidden = false; }
  function probe(clip) {
    const audio = new Audio();
    const url = URL.createObjectURL(clip.file);
    audio.preload = "metadata";
    audio.onloadedmetadata = () => { clip.seconds = audio.duration; URL.revokeObjectURL(url); renderClips(); };
    audio.onerror = () => { clip.unreadable = true; URL.revokeObjectURL(url); renderClips(); };
    audio.src = url;
  }
  function renderClips() {
    clipList.replaceChildren(...form.clips.map((clip, index) => {
      let note = "Reading length";
      let warn = false;
      if (clip.unreadable) { note = "Can't read this file"; warn = true; }
      else if (clip.seconds !== null) {
        note = formatDuration(clip.seconds);
        if (clip.seconds < IDEAL[0]) { note += ", short: clones from very short clips sound less like the voice"; warn = true; }
        else if (clip.seconds > IDEAL[1]) { note += ", long: trim to the cleanest 30 seconds"; warn = true; }
      }
      return h("li", { class: warn ? "warn" : "" },
        icon(warn ? "alert" : "voice", "sm"),
        h("span", { class: "grow" }, h("strong", { text: clip.file.name }), " ", h("span", { class: "dur", text: note })),
        h("button", { class: "btn quiet small", type: "button", "aria-label": `Remove ${clip.file.name}`,
          onClick: () => { form.clips.splice(index, 1); renderClips(); } }, icon("x", "sm")));
    }));
  }

  // ---- submit + progress ----
  const formError = h("div", { class: "notice bad", hidden: true, role: "alert" }, icon("alert"), h("div", { class: "notice-body" }));
  const submit = h("button", { class: "btn primary", type: "submit" }, "Add character");
  const cancel = h("button", { class: "btn quiet", type: "button", onClick: () => onClose() }, "Cancel");
  const progress = h("div", { class: "progress", hidden: true, role: "status" });
  const actions = h("div", { class: "form-actions full" }, submit, cancel);

  function showError(message) {
    formError.hidden = false;
    formError.querySelector(".notice-body").replaceChildren(h("p", { text: message }));
    formError.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  const el = h("form", { class: "panel addbox", novalidate: true, "aria-labelledby": "add-title", onSubmit: onSubmit },
    h("div", { class: "addbox-head" },
      h("div", {}, h("h2", { id: "add-title", text: "Add a character" }),
        h("p", { class: "help", text: "A character is a picture and a voice. The writer can also adopt their personality." })),
      h("button", { class: "btn quiet small", type: "button", onClick: () => onClose(), "aria-label": "Close" }, icon("x", "sm"))),
    h("div", { class: "form-grid" },
      h("div", { class: "field" },
        h("label", { for: ids.name, text: "Name" }), nameInput,
        h("p", { class: "help", id: `${ids.name}-help`, text: "Shown in the gallery. Used for the folder name too." }), nameError),
      h("div", { class: "field" }, h("span", { class: "label", text: "Picture" }), avatarDrop, avatarWarn, avatarError),
      h("div", { class: "field full" }, h("span", { class: "label", text: "Voice" }), seg, clonePanel, profilePanel, defaultPanel),
      h("div", { class: "field full" },
        h("label", { for: ids.persona, text: "Personality (optional)" }), persona,
        h("p", { class: "help", text: "How should the scripts sound? One sentence is plenty. Facts always stay accurate." })),
      h("div", { class: "full stack" }, formError, progress, actions)));

  setMode(form.mode);

  async function onSubmit(event) {
    event.preventDefault();
    if (form.busy) return;
    formError.hidden = true;
    nameError.hidden = true;
    nameInput.removeAttribute("aria-invalid");
    const name = nameInput.value.trim();
    let firstInvalid = null;
    const invalid = (control) => { firstInvalid = firstInvalid || control; };
    if (!name) {
      nameError.textContent = "Give the character a name.";
      nameError.hidden = false;
      nameInput.setAttribute("aria-invalid", "true");
      invalid(nameInput);
    }
    if (!form.avatar) {
      avatarError.textContent = "Choose a picture for the character.";
      avatarError.hidden = false;
      invalid(avatarInput);
    }
    if (form.mode === "clone" && !form.clips.length) { showClipError("Add at least one voice clip, or choose another voice option."); invalid(clipInput); }
    if (form.mode === "profile" && !profileSelect.value) { showError("Choose one of your Voicebox voices, or pick another voice option."); invalid(profileSelect.hidden ? seg.querySelector("input") : profileSelect); }
    if (firstInvalid) { firstInvalid.focus(); return; }

    const data = new FormData();
    data.set("name", name);
    data.set("persona", persona.value.trim());
    data.set("voice_mode", form.mode);
    if (form.mode === "profile") data.set("profile_id", profileSelect.value);
    data.set("avatar", form.avatar, "avatar");
    if (form.mode === "clone") for (const clip of form.clips) data.append("voice", clip.file, "clip");
    setBusy(true);
    try {
      const { job } = await api.upload("/api/characters", data);
      remember(job.id);
      startedAt = Date.now();
      poll(job.id, name);
    } catch (err) {
      setBusy(false);
      showError(err.message);
    }
  }

  function setBusy(busy) {
    form.busy = busy;
    for (const control of el.querySelectorAll("input, textarea, select, button")) {
      if (control !== cancel) control.disabled = busy && control !== cancel;
    }
    submit.disabled = busy;
    progress.hidden = !busy;
    actions.hidden = busy;
    if (busy) {
      const label = h("strong", { text: form.mode === "clone" ? "Cloning the voice" : "Adding the character" });
      const sub = h("span", { class: "help", id: "add-sub" });
      progress.replaceChildren(
        h("div", { class: "progress-head" }, spinner(), h("div", {}, label, h("br"), sub)),
        h("div", { class: "bar", "aria-hidden": "true" }, h("i")),
        h("p", { class: "help", text: form.mode === "clone"
          ? "This usually takes one to three minutes. You can leave this page; it keeps going in the background."
          : "This should only take a moment." }));
      progress._sub = sub;
    }
  }

  async function poll(jobId, name) {
    clearTimeout(pollTimer);
    try {
      const data = await api.get(`/api/jobs/${jobId}`);
      const job = data.job;
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      const line = data.log.text.split("\n").map((s) => s.trim()).filter(Boolean).pop();
      if (progress._sub) progress._sub.textContent = `${formatDuration(seconds)} elapsed${line && !line.startsWith("Traceback") ? `. ${line.replace(/^[!\s]+/, "")}` : ""}`;
      if (job.state === "running") { pollTimer = setTimeout(() => poll(jobId, name), 1500); return; }
      remember(null);
      const outcome = data.outcome;
      if (job.state === "done" && outcome?.ok) {
        await loadCharacters();
        setDraft("CHARACTER", outcome.slug);
        toast(`${name} added and selected. Save your changes to use them from the next run.`);
        onAdded(outcome.slug);
        return;
      }
      setBusy(false);
      showError(outcome?.message || (job.state === "interrupted"
        ? "The panel lost track of this job. Check whether the character appears in the gallery, then try again."
        : "Couldn't add the character. Check that Voicebox is running and try again."));
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) { pollTimer = setTimeout(() => poll(jobId, name), 3000); return; }
      setBusy(false);
      showError(err.message);
    }
  }

  // resume a job started before a page reload
  const resumeId = recall();
  if (resumeId) {
    setBusy(true);
    startedAt = Date.now();
    poll(resumeId, "The character");
  }

  return {
    el,
    focus: () => nameInput.focus(),
    dispose: () => clearTimeout(pollTimer),
    resuming: Boolean(resumeId),
  };
}
