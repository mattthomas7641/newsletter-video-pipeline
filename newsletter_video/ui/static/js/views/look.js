import { api } from "../api.js";
import { h, icon, spinner, plural } from "../dom.js";
import { savePill, notYetNote } from "../components.js";
import { state, subscribe, value, setDraft, loadSetup } from "../store.js";
import { addCharacterPanel } from "../addcharacter.js";

// Schematic 9:16 frames that explain each style. They are diagrams, not screenshots.
const FRAMES = {
  avatar: `<svg viewBox="0 0 54 96" role="img" aria-label="Avatar style: a narrator character over gameplay footage">
    <rect class="f-bg" width="54" height="96"/>
    <rect class="f-bg2" x="0" y="46" width="54" height="50"/><rect class="f-bg2" x="30" y="0" width="24" height="46" opacity=".5"/>
    <rect class="f-accent" x="6" y="7" width="20" height="5" rx="2.5"/>
    <rect class="f-panel" x="6" y="17" width="42" height="26" rx="4"/>
    <rect class="f-soft" x="10" y="22" width="34" height="3" rx="1.5"/><rect class="f-soft" x="10" y="29" width="26" height="3" rx="1.5"/><rect class="f-accent" x="10" y="36" width="16" height="3" rx="1.5"/>
    <circle class="f-ink" cx="14" cy="62" r="6"/><path class="f-ink" d="M4 96c0-12 4-22 10-22s10 10 10 22Z"/>
    <rect class="f-panel" x="8" y="48" width="38" height="5" rx="2.5" opacity=".9"/></svg>`,
  explainer: `<svg viewBox="0 0 54 96" role="img" aria-label="Explainer style: clean animated graphics with no character">
    <rect class="f-panel" width="54" height="96"/>
    <rect class="f-accent" x="6" y="8" width="20" height="5" rx="2.5"/>
    <rect class="f-ink" x="6" y="19" width="42" height="5" rx="2.5"/><rect class="f-ink" x="6" y="28" width="30" height="5" rx="2.5"/>
    <rect class="f-accent" x="6" y="42" width="8" height="26" rx="2"/><rect class="f-soft" x="19" y="52" width="8" height="16" rx="2"/><rect class="f-soft" x="32" y="46" width="8" height="22" rx="2"/><rect class="f-soft" x="45" y="58" width="3" height="10" rx="1.5"/>
    <rect class="f-bg" x="6" y="76" width="42" height="12" rx="6"/><rect class="f-ink" x="12" y="80" width="30" height="4" rx="2"/></svg>`,
};

function frame(kind) {
  const holder = h("span", { class: "frame" });
  holder.innerHTML = FRAMES[kind]; // static markup defined above, no user data
  return holder;
}

export function mountLook(root, ctx) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  const sampleUi = new Map(); // slug -> {button, status}
  let audio = null;
  let playingSlug = null;
  let addPanel = null;

  // ---- header ----
  const head = h("header", { class: "page-head" },
    h("div", {}, h("h1", { text: "Look and voice" }),
      h("p", { class: "lede", text: "Choose what your videos look like and who narrates them. Saved choices apply from the next video, including the 7:00 AM run." })),
    savePill(onCleanup));

  // ---- style cards ----
  const styleName = "video-style";
  const styleCard = (kind, title, text) => h("label", { class: "style-card" },
    h("input", { type: "radio", name: styleName, value: kind, onChange: () => setDraft("VIDEO_STYLE", kind) }),
    h("span", { class: "face" }, frame(kind),
      h("span", {}, h("h3", {}, title, h("span", { class: "tick" }, icon("check"))), h("p", { text }))));
  const styleSection = h("section", { class: "section", "aria-labelledby": "style-h" },
    h("div", { class: "section-head" }, h("h2", { id: "style-h", text: "Video style" })),
    h("div", { class: "style-cards", role: "radiogroup", "aria-labelledby": "style-h" },
      styleCard("avatar", "Avatar", "A narrator character sits at the bottom of the screen, usually over gameplay footage."),
      styleCard("explainer", "Explainer", "Clean animated graphics and text, with no character on screen. Voice only.")),
    h("div", { id: "style-note" }));

  // ---- narrator gallery ----
  const narratorTitle = h("h2", { id: "narrator-h" });
  const narratorText = h("p");
  const gallery = h("div", { class: "chars", role: "radiogroup", "aria-labelledby": "narrator-h" });
  const missingNote = h("div", { id: "missing-note" });
  const addButton = h("button", { class: "btn small", type: "button", onClick: () => openAdd() }, icon("plus", "sm"), "Add a character");
  const narratorSection = h("section", { class: "section", "aria-labelledby": "narrator-h" },
    h("div", { class: "section-head split" }, h("div", {}, narratorTitle, narratorText), addButton), missingNote, gallery);
  const addHost = h("div");
  narratorSection.append(addHost);

  // ---- background (avatar only) ----
  const bgName = "background";
  const bgSeg = h("div", { class: "seg", role: "radiogroup", "aria-labelledby": "bg-h" },
    [["gameplay", "Gameplay footage"], ["none", "Plain background"]].map(([v, label]) => h("label", {},
      h("input", { type: "radio", name: bgName, value: v, onChange: () => setDraft("BACKGROUND_STYLE", v) }),
      h("span", { class: "opt", text: label }))));
  const bgNote = h("div", { id: "bg-note" });
  const bgSection = h("section", { class: "section", "aria-labelledby": "bg-h" },
    h("div", { class: "section-head" }, h("h2", { id: "bg-h", text: "Background" }),
      h("p", { text: "What plays behind the cards and captions." })), bgSeg, bgNote);

  // ---- theme (explainer only) ----
  const themeName = "theme";
  const themeSeg = h("div", { class: "seg", role: "radiogroup", "aria-labelledby": "theme-h" },
    [["light", "Light"], ["dark", "Dark"]].map(([v, label]) => h("label", {},
      h("input", { type: "radio", name: themeName, value: v, onChange: () => setDraft("VIDEO_THEME", v) }),
      h("span", { class: "opt" }, h("span", { class: `swatch ${v}` }), label))));
  const themeNote = h("div");
  const themeSection = h("section", { class: "section", "aria-labelledby": "theme-h" },
    h("div", { class: "section-head" }, h("h2", { id: "theme-h", text: "Colors" }),
      h("p", { text: "Light or dark graphics for explainer videos." })), themeSeg, themeNote);

  root.append(head, styleSection, narratorSection, bgSection, themeSection);

  // ---- characters gallery ----
  function characterCard(c) {
    const id = `char-${c.slug || "default"}`;
    const input = h("input", { type: "radio", name: "character", id, value: c.slug,
      onChange: () => setDraft("CHARACTER", c.slug) });
    const art = h("span", { class: "art" },
      c.avatar_url ? h("img", { src: c.avatar_url, alt: `${c.name} avatar`, loading: "lazy" })
        : h("span", { class: "none", text: c.is_default ? "No image in assets/character" : "No image" }),
      h("span", { class: "tick" }, icon("check")));
    const voiceOk = c.voice.kind === "cloned" || c.voice.kind === "voicebox";
    const label = h("label", { class: "pick", for: id }, art,
      h("span", {}, h("span", { class: "name", text: c.name }),
        h("span", { class: `voice${voiceOk ? "" : " plain"}` }, icon("voice"), c.voice.label),
        c.persona ? h("span", { class: "persona", text: c.persona }) : null));
    const button = h("button", { class: "btn small", type: "button", onClick: () => hear(c) });
    const status = h("p", { class: "err", hidden: true, role: "alert" });
    const bar = h("div", { class: "playing-bar", hidden: true }, h("i"));
    sampleUi.set(c.slug, { button, status, bar });
    paintButton(c.slug, "idle");
    return h("div", { class: "char", dataset: { slug: c.slug } }, input, label, h("div", { class: "foot" }, button, bar, status));
  }

  function paintButton(slug, mode) {
    const ui = sampleUi.get(slug);
    if (!ui) return;
    ui.button.replaceChildren();
    ui.button.disabled = mode === "loading";
    ui.button.setAttribute("aria-busy", mode === "loading" ? "true" : "false");
    ui.bar.hidden = mode !== "playing";
    if (mode === "loading") ui.button.append(spinner(), "Making a sample");
    else if (mode === "playing") ui.button.append(icon("stop", "sm"), "Stop");
    else ui.button.append(icon("voice", "sm"), "Hear voice");
  }

  function stopAudio() {
    if (audio) { audio.pause(); audio = null; }
    if (playingSlug !== null) paintButton(playingSlug, "idle");
    playingSlug = null;
  }

  async function hear(c) {
    const ui = sampleUi.get(c.slug);
    if (playingSlug === c.slug) { stopAudio(); return; }
    stopAudio();
    ui.status.hidden = true;
    paintButton(c.slug, "loading");
    try {
      let res = await api.post(`/api/characters/${encodeURIComponent(c.slug || "default")}/sample`);
      let url = res.url;
      while (res.state === "running") {
        await new Promise((r) => setTimeout(r, 1200));
        if (!root.isConnected) return;
        const data = await api.get(`/api/jobs/${res.job.id}`);
        if (data.job.state === "running") continue;
        if (data.job.state !== "done" || !data.job.url) {
          const tail = (data.job.error_tail || []).filter(Boolean).pop() || "";
          throw new Error(/Voicebox|Connection|refused/i.test(tail)
            ? "Voicebox isn't running. Open the Voicebox app and try again."
            : "Couldn't make a sample. Check the terminal for details.");
        }
        url = data.job.url;
        break;
      }
      playUrl(c.slug, url);
    } catch (err) {
      paintButton(c.slug, "idle");
      ui.status.textContent = err.message;
      ui.status.hidden = false;
    }
  }

  function playUrl(slug, url) {
    audio = new Audio(url);
    playingSlug = slug;
    const ui = sampleUi.get(slug);
    paintButton(slug, "playing");
    audio.addEventListener("timeupdate", () => {
      if (audio && audio.duration) ui.bar.firstChild.style.setProperty("width", `${(audio.currentTime / audio.duration) * 100}%`);
    });
    audio.addEventListener("ended", stopAudio);
    audio.addEventListener("error", () => {
      stopAudio();
      ui.status.textContent = "The sample couldn't be played.";
      ui.status.hidden = false;
    });
    audio.play().catch(() => { stopAudio(); ui.status.textContent = "Your browser blocked playback. Click Hear voice again."; ui.status.hidden = false; });
  }

  let galleryKey = "";
  function renderGallery() {
    const key = JSON.stringify(state.characters);
    if (key === galleryKey) return;
    galleryKey = key;
    stopAudio();
    sampleUi.clear();
    gallery.replaceChildren();
    if (!state.loaded) {
      gallery.append(...[1, 2, 3].map(() => h("div", { class: "char sk skeleton" })));
      return;
    }
    for (const c of state.characters) gallery.append(characterCard(c));
    syncSelection();
  }

  function openAdd() {
    if (!addPanel) {
      addPanel = addCharacterPanel({
        onClose: () => { addHost.hidden = true; },
        onAdded: () => { addHost.hidden = true; addPanel.dispose(); addPanel = null; addHost.replaceChildren(); gallery.scrollIntoView({ block: "nearest", behavior: "smooth" }); },
      });
      addHost.replaceChildren(addPanel.el);
    }
    addHost.hidden = false;
    addPanel.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    addPanel.focus();
  }
  onCleanup(() => addPanel?.dispose());

  // ---- keep controls in sync with the draft ----
  function syncSelection() {
    const style = value("VIDEO_STYLE");
    const selected = value("CHARACTER");
    for (const input of root.querySelectorAll(`input[name="${styleName}"]`)) input.checked = input.value === style;
    for (const input of bgSeg.querySelectorAll("input")) input.checked = input.value === value("BACKGROUND_STYLE");
    for (const input of themeSeg.querySelectorAll("input")) input.checked = input.value === value("VIDEO_THEME");
    const known = state.characters.some((c) => c.slug === selected);
    for (const card of gallery.querySelectorAll(".char")) {
      const on = card.dataset.slug === selected && known;
      card.classList.toggle("selected", on);
      card.querySelector("input").checked = on;
    }
    gallery.classList.toggle("compact", style === "explainer");

    narratorTitle.textContent = style === "avatar" ? "Narrator" : "Narrator voice";
    narratorText.textContent = style === "avatar"
      ? "Pick who appears on screen. Their voice comes with them, so there is no separate voice to choose."
      : "Explainer videos don't show a character, but whoever you pick still supplies the voice and writing style.";
    bgSection.hidden = style !== "avatar";
    themeSection.hidden = style !== "explainer";

    missingNote.replaceChildren();
    if (state.loaded && selected && !known) {
      missingNote.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: `The saved narrator "${selected}" isn't in your characters folder any more.` }),
        h("p", { text: "Pick another one below, or the next run will stop with an error." }))));
    }
    const supported = state.settings?.supported || {};
    document.getElementById("style-note")?.replaceChildren(notYetNote(supported.VIDEO_STYLE) || "");
    themeNote.replaceChildren((supported.VIDEO_STYLE ? notYetNote(supported.VIDEO_THEME) : null) || "");

    bgNote.replaceChildren();
    const gameplay = state.setup?.checks.find((c) => c.id === "gameplay");
    if (value("BACKGROUND_STYLE") === "gameplay" && gameplay && !gameplay.ok) {
      bgNote.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "There's no gameplay footage on this computer yet." }),
        h("p", { text: "Videos will use the plain graph-paper background until you add .mp4 clips to assets/background." }))));
    }
  }

  const update = () => { renderGallery(); syncSelection(); };
  update();
  onCleanup(subscribe(update));
  loadSetup().catch(() => {});

  return () => {
    stopAudio();
    cleanup.forEach((fn) => fn());
    root.replaceChildren();
  };
}
