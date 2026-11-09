// The one style control used everywhere a video's style is chosen: the
// default for automatic videos and the style of a single video.
//
//   Explainer | Avatar
//   Explainer -> Clean paper or Dark glass, then two colours for it: the
//                background and the animation colour (swatches or any hex).
//   Avatar    -> a few avatars and one standard voice (shown, not chosen).
//
// It holds a complete value {style, preset, bg, accent, character}; "" for
// bg / accent means "the look's own colour". It never saves anything: the
// owner of the control decides what a change means.
import { h, icon, nextId } from "./dom.js";
import { state, presets, presetInfo, effectivePresetId } from "./store.js";
import { presetThumb } from "./presets.js";
import { addCharacterPanel } from "./addcharacter.js";
import { fitColors, normaliseHex, inkFor } from "./colors.js";

export const STYLE_NAMES = { explainer: "Explainer", avatar: "Avatar" };

/** The value a plan's resolved look stands for. */
export function valueFromLook(look) {
  return { style: look.style || "explainer", preset: effectivePresetId(look.preset), bg: look.bg || "", accent: look.accent || "", character: look.character || "", mode: "one", rotate: [] };
}

export function sameValue(a, b) {
  return a.style === b.style && effectivePresetId(a.preset) === effectivePresetId(b.preset) && (a.bg || "") === (b.bg || "")
    && (a.accent || "") === (b.accent || "") && (a.character || "") === (b.character || "")
    && (a.mode || "one") === (b.mode || "one") && (a.rotate || []).join(",").toLowerCase() === (b.rotate || []).join(",").toLowerCase();
}

/** The words for a value: "Explainer · Clean paper · sage background, teal animations". */
export function describeValue(v) {
  if (v.style === "avatar") return `Avatar · ${characterLabel(v.character)}`;
  const p = presetInfo(effectivePresetId(v.preset));
  const bits = ["Explainer", p ? p.name : "Clean paper"];
  if (v.mode && v.mode !== "one" && v.rotate?.length) bits.push(`${v.bg ? `background ${v.bg}, ` : ""}animations ${v.mode === "random" ? "random from" : "alternate through"} ${v.rotate.length} colours`);
  else if (v.bg || v.accent) bits.push([v.bg ? `background ${v.bg}` : "", v.accent ? `animations ${v.accent}` : ""].filter(Boolean).join(", "));
  return bits.join(" · ");
}

/** A small stand-in frame in a style's colours: the ground, the ink the look picks for it, the accent. */
export function previewTile(v, { small = false } = {}) {
  if (v.style === "avatar") {
    const c = state.characters.find((x) => x.slug === (v.character || ""));
    return h("span", { class: `sp-preview avatar${small ? " small" : ""}`, role: "img", "aria-label": `Avatar: ${characterLabel(v.character)}` },
      c?.avatar_url ? h("img", { src: c.avatar_url, alt: "" }) : icon("palette"));
  }
  const p = presetInfo(effectivePresetId(v.preset));
  const dark = Boolean(p?.cinematic);
  const colors = p?.colors || {};
  const fit = fitColors(dark, v.bg, v.accent, colors.bg);
  const bg = fit.bg || colors.bg || (dark ? "#050509" : "#f6f4ee");
  const accent = fit.accent || colors.accent || "#1f49d6";
  return h("span", { class: `sp-preview${dark ? " dark" : ""}${small ? " small" : ""}`, style: `--bg:${bg};--ink:${inkFor(bg)};--ac:${accent}`, role: "img",
    "aria-label": `${p ? p.name : "Clean paper"} with background ${bg} and animation colour ${accent}` },
  h("span", { class: "sp-p-kick", text: "AI CHIPS" }), h("span", { class: "sp-p-icon" }),
  h("span", { class: "sp-p-title" }, "Compute is the ", h("em", { text: "contest" })),
  h("span", { class: "sp-p-bars" }, h("i"), h("i"), h("i")));
}

export function characterLabel(slug) {
  const c = state.characters.find((x) => x.slug === (slug || ""));
  return c ? (c.slug ? c.name : "Default avatar") : slug || "Default avatar";
}

const standardVoice = () => {
  const d = state.characters.find((c) => c.is_default) || state.characters[0];
  return d?.voice?.label || "Standard narrator voice";
};

/** Builds the control. `onChange(value)` fires after each change the person makes. */
export function stylePicker({ value, onChange = () => {}, name = nextId("sp"), allowAdd = true, rotation = false }) {
  // `rotation`: also offer One colour / Alternate / Random for the animation colour (a batch of a lineup)
  let v = { ...value };
  const root = h("div", { class: "sp" });
  const notesHost = h("div", { class: "sp-notes", "aria-live": "polite" });
  let addHost = null;

  const emit = () => { paint(); onChange({ ...v }); };

  // ---- style: Explainer | Avatar ----
  const styleSeg = h("div", { class: "seg sp-style", role: "radiogroup", "aria-label": "Video style" },
    ["explainer", "avatar"].map((s) => h("label", {},
      h("input", { type: "radio", name: `${name}-style`, value: s, onChange: () => { v.style = s; emit(); } }),
      h("span", { class: "opt", text: STYLE_NAMES[s] }))));

  const body = h("div", { class: "sp-body" });
  root.append(styleSeg, body, notesHost);

  // ---- explainer: look cards + colours ----
  function explainerPane() {
    const list = presets();
    const current = effectivePresetId(v.preset);
    const cards = h("div", { class: "sp-looks", role: "radiogroup", "aria-label": "Look" },
      list.map((p) => {
        const on = p.id === current;
        const id = `${name}-look-${p.id}`;
        return h("label", { class: `sp-look${on ? " on" : ""}`, for: id },
          h("input", { type: "radio", name: `${name}-look`, id, value: p.id, checked: on,
            onChange: () => { v.preset = p.id; v.bg = ""; v.accent = ""; v.mode = rotation && p.cinematic ? "alternate" : "one"; v.rotate = v.mode === "one" ? [] : paletteFor(p); emit(); } }),
          presetThumb(p),
          h("span", { class: "sp-look-body" }, h("strong", { text: p.name }), h("span", { class: "help", text: p.good_for || p.description })),
          h("span", { class: "tick", "aria-hidden": "true" }, icon("check")));
      }));
    const p = presetInfo(current);
    const colors = p?.colors || {};
    const dark = Boolean(p?.cinematic);

    const control = (key, label, swatches, own, hint) => {
      const chosen = (v[key] || own || "").toLowerCase();
      const group = h("div", { class: "sp-swatches", role: "radiogroup", "aria-label": label },
        swatches.map((s) => {
          const on = chosen === s.hex.toLowerCase();
          return h("button", { class: `sw${on ? " on" : ""}`, type: "button", role: "radio", "aria-checked": String(on), "aria-label": `${s.name} ${s.hex}`, title: `${s.name} ${s.hex}`,
            style: `--c:${s.hex}`, onClick: () => { v[key] = s.hex.toLowerCase() === (own || "").toLowerCase() ? "" : s.hex; emit(); } },
          on ? icon("check") : null);
        }));
      const hexId = `${name}-${key}-hex`;
      const hex = h("input", { class: "input sp-hex", id: hexId, type: "text", value: v[key] || "", placeholder: own, maxlength: 7, spellcheck: false, autocomplete: "off",
        "aria-label": `${label}, custom colour as hex`, onChange: () => {
          const n = normaliseHex(hex.value);
          if (hex.value.trim() === "") { v[key] = ""; emit(); } else if (n) { v[key] = n === (own || "").toLowerCase() ? "" : n; emit(); } else { hex.setAttribute("aria-invalid", "true"); hex.title = "Use a colour like #1f49d6"; }
        }, onInput: () => { hex.removeAttribute("aria-invalid"); hex.title = ""; } });
      const picker = h("input", { type: "color", class: "sp-color", value: normaliseHex(v[key] || own) || "#000000", "aria-label": `${label}, pick any colour`,
        onChange: () => { v[key] = picker.value.toLowerCase() === (own || "").toLowerCase() ? "" : picker.value.toLowerCase(); emit(); } });
      return h("div", { class: "sp-color-row" },
        h("div", { class: "sp-color-head" }, h("span", { class: "label", text: label }), h("span", { class: "help", text: hint })),
        group,
        h("div", { class: "sp-custom" }, picker, hex,
          v[key] ? h("button", { class: "btn-link", type: "button", onClick: () => { v[key] = ""; emit(); } }, "Back to the look's own") : h("span", { class: "faint small", text: "the look's own" })));
    };

    const rotating = rotation && v.mode && v.mode !== "one" && (v.rotate || []).length >= 2;
    const preview = previewTile({ ...v, style: "explainer", accent: rotating ? v.rotate[0] : v.accent });
    const rotationRow = rotation ? rotationControl(p) : null;
    return [cards, h("div", { class: "sp-colors" },
      h("div", { class: "sp-colors-main" },
        control("bg", "Background", colors.bg_swatches || [], colors.bg, dark ? "A dark ground, so the light text stays readable." : "A light ground, so the dark text stays readable."),
        rotationRow,
        rotating ? null : control("accent", "Animation colour", colors.accent_swatches || [], colors.accent,
          dark ? "The neon edge light, glow and 3D accents are built from it." : "Pictogram strokes, highlights, bars and emphasis.")),
      h("div", { class: "sp-preview-wrap" }, preview, h("span", { class: "help", text: "Live preview of the two colours." })))];
  }

  // ---- the animation colour mode: one colour, alternate through a palette, or a random colour per video ----
  const paletteFor = (p) => (p?.cinematic ? [...(state.looks?.rotation_default || [])] : (p?.colors?.accent_swatches || []).slice(0, 5).map((x) => x.hex));
  const MODES = [["one", "One colour"], ["alternate", "Alternate"], ["random", "Random"]];
  function rotationControl(p) {
    const mode = v.mode || "one";
    const seg = h("div", { class: "seg sp-mode", role: "radiogroup", "aria-label": "Animation colour mode" },
      MODES.map(([val, label]) => h("label", {},
        h("input", { type: "radio", name: `${name}-mode`, value: val, checked: mode === val,
          onChange: () => { v.mode = val; if (val !== "one" && (v.rotate || []).length < 2) v.rotate = paletteFor(p); emit(); } }),
        h("span", { class: "opt", text: label }))));
    const head = h("div", { class: "sp-rot-head" }, h("span", { class: "label", text: "Colour mode" }), seg);
    if (mode === "one") return h("div", { class: "sp-color-row sp-rot" }, head);
    const list = v.rotate || [];
    const swatches = h("div", { class: "sp-swatches", role: "list", "aria-label": "Animation colours, in the order the videos use them" },
      list.map((hex, i) => h("span", { role: "listitem", class: "sp-rot-item" },
        h("button", { class: "sw", type: "button", style: `--c:${hex}`, "aria-label": `Colour ${i + 1}, ${hex}. Remove it`, title: `${hex}. Click to remove`,
          disabled: list.length <= 2, onClick: () => { v.rotate = list.filter((_, k) => k !== i); emit(); } }, h("span", { class: "sw-n", text: String(i + 1) })))));
    const adder = h("input", { type: "color", class: "sp-color", value: "#ffffff", "aria-label": "Add a colour",
      disabled: list.length >= 8, onChange: () => { const hex = normaliseHex(adder.value); if (hex && !list.includes(hex)) { v.rotate = [...list, hex]; emit(); } } });
    return h("div", { class: "sp-color-row sp-rot" }, head,
      h("p", { class: "help", text: mode === "alternate"
        ? `Video 1 of the batch uses the first colour, video 2 the second, and so on, starting again after ${list.length}.`
        : "Each video gets a random colour from this palette, chosen per day, so a re-render keeps it. Never the same twice in a row." }),
      swatches, h("div", { class: "sp-custom" }, adder, h("span", { class: "faint small", text: list.length >= 8 ? "8 colours is the most" : list.length <= 2 ? "Pick a colour to add it; two is the fewest" : "Pick a colour to add it" })));
  }

  // ---- avatar: a few avatars and one standard voice ----
  function avatarPane() {
    const chars = state.characters;
    const cards = h("div", { class: "sp-chars", role: "radiogroup", "aria-label": "Avatar" },
      chars.map((c) => {
        const on = (v.character || "") === c.slug;
        const id = `${name}-char-${c.slug || "default"}`;
        return h("label", { class: `sp-char${on ? " on" : ""}`, for: id },
          h("input", { type: "radio", name: `${name}-char`, id, value: c.slug, checked: on, onChange: () => { v.character = c.slug; emit(); } }),
          h("span", { class: "art" }, c.avatar_url ? h("img", { src: c.avatar_url, alt: "", loading: "lazy" }) : h("span", { class: "none", text: "No image" }),
            h("span", { class: "tick", "aria-hidden": "true" }, icon("check"))),
          h("strong", { text: c.slug ? c.name : "Default avatar" }));
      }),
      allowAdd ? h("button", { class: "sp-char add", type: "button", onClick: openAdd }, h("span", { class: "art" }, icon("plus")), h("strong", { text: "Add avatar" })) : null);
    addHost = h("div", { class: "sp-add" });
    return [cards, addHost, h("p", { class: "sp-voice" }, icon("voice", "sm"), h("span", {}, h("strong", { text: "Voice: " }), standardVoice()),
      h("span", { class: "faint", text: " · one standard voice for every avatar. " }), h("a", { class: "link", href: "#/settings", text: "Change it in Settings" }))];
  }

  let adder = null;
  function openAdd() {
    if (adder) return;
    adder = addCharacterPanel({
      selectNew: false,
      onClose: () => { adder?.dispose(); adder = null; addHost?.replaceChildren(); },
      onAdded: (slug) => { adder?.dispose(); adder = null; v.character = slug; emit(); },
    });
    addHost.replaceChildren(adder.el);
    adder.focus();
  }

  function paint() {
    for (const input of styleSeg.querySelectorAll("input")) input.checked = input.value === v.style;
    if (adder && v.style === "avatar") return; // don't tear down an open "add avatar" form
    body.replaceChildren(...(v.style === "avatar" ? avatarPane() : explainerPane()));
    notesHost.replaceChildren();
    if (v.style === "explainer") {
      const p = presetInfo(effectivePresetId(v.preset));
      const fit = fitColors(Boolean(p?.cinematic), v.bg, v.accent, p?.colors?.bg);
      for (const note of fit.notes) notesHost.append(h("div", { class: "notice warn" }, icon("info"), h("div", { class: "notice-body" }, h("p", { text: note }))));
    }
  }

  paint();
  return Object.assign(root, {
    get: () => ({ ...v }),
    set: (next) => { v = { ...next }; paint(); },
    dispose: () => { adder?.dispose(); adder = null; },
  });
}


/** The fields of `v` that differ from `def`: what a single video stores so it still follows the default for the rest. */
export function diffFromDefault(v, def) {
  const out = {};
  if (v.style !== def.style) out.style = v.style;
  if (v.style === "avatar") {
    if ((v.character || "") !== (def.character || "")) out.character = v.character || "";
    return out;
  }
  const samePreset = effectivePresetId(v.preset) === effectivePresetId(def.preset);
  if (!samePreset) out.preset = effectivePresetId(v.preset);
  for (const key of ["bg", "accent"]) {
    if (samePreset ? (v[key] || "") !== (def[key] || "") : v[key]) out[key] = v[key] || "";
  }
  return out;
}
