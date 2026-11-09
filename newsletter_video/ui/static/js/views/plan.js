// The plan panel: a day's planned videos, each with its look, its previews
// and the explicit actions that cost time or money. The look vocabulary
// (chips, labels, preview blocks) is shared with the workbench.
import { h, append, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { toast, ago, costTag } from "../components.js";
import { state, subscribe, loadPlan, isRunning, isTestRef } from "../store.js";
import { startPreview, saveLook, openStudio, stopJob, rewriteVideo, renderFinal, attempt, approxRender } from "../actions.js";
import { presetName } from "../presets.js";
import { stylePicker, valueFromLook, sameValue, diffFromDefault, describeValue, previewTile } from "../stylepicker.js";

export const STYLE_LABEL = { avatar: "Avatar", explainer: "Explainer" };
export const THEME_LABEL = { light: "Light", dark: "Dark", cinematic: "Cinematic" };
export const BG_LABEL = { gameplay: "Gameplay", none: "Plain" };

export function characterName(slug) {
  if (!slug) return "Default narrator";
  const c = state.characters.find((x) => x.slug === slug);
  return c ? c.name : slug;
}

/** One line naming a resolved look: "Explainer · Cinematic dark glass · Cinematic theme · Default narrator". */
export function lookLabel(resolved) {
  const parts = [STYLE_LABEL[resolved.style] || resolved.style];
  if (resolved.style === "avatar") parts.push(`${BG_LABEL[resolved.background] || resolved.background} background`);
  else parts.push(presetName(resolved.preset), `${THEME_LABEL[resolved.theme] || resolved.theme} theme`);
  parts.push(characterName(resolved.character));
  return parts.join(" · ");
}

export function lookChips(resolved, overrides) {
  const set = (k) => overrides && overrides[k] !== undefined;
  const chip = (text, k) => h("span", { class: `chip${set(k) ? " active" : ""}`, title: set(k) ? "Set for this video" : "From your defaults" }, text);
  const chips = [chip(`${STYLE_LABEL[resolved.style] || resolved.style} style`, "style"), chip(characterName(resolved.character), "character")];
  if (resolved.style === "avatar") chips.push(chip(`${BG_LABEL[resolved.background] || resolved.background} background`, "background"));
  else chips.push(chip(`${presetName(resolved.preset)} look`, "preset"), chip(`${THEME_LABEL[resolved.theme] || resolved.theme} theme`, "theme"));
  return chips;
}

export function stillsBlock(s, { heading = "Storyboard" } = {}) {
  const estimated = s.timing === "estimated timing";
  return h("figure", { class: "sb" },
    h("a", { href: s.url, target: "_blank", rel: "noopener", "aria-label": "Open the storyboard full size" },
      h("img", { src: `${s.url}?t=${Math.round(s.at || 0)}`, alt: "Storyboard: one frame per scene", loading: "lazy" })),
    h("figcaption", { class: "sb-cap" },
      h("span", {}, h("strong", { text: heading }), ` · ${plural((s.frames || []).length, "frame")} · ready in ${Math.round(s.seconds || 0)} s`),
      h("span", { class: `chip${estimated ? " warn" : " good"}`, text: estimated ? "Estimated timing" : s.timing === "voiced" ? "Voiced" : "Real timing from the saved voice" }),
      h("span", { class: "faint", text: `about ${formatDuration(s.duration || 0)} long` }),
      s.at ? h("span", { class: "faint", text: ago(s.at) }) : null));
}

export function draftBlock(d) {
  const estimated = d.timing === "estimated timing";
  return h("div", { class: "draft" },
    h("video", { src: `${d.url}?t=${Math.round(d.at || 0)}`, controls: true, preload: "metadata", playsinline: true, "aria-label": "Draft clip" }),
    h("div", { class: "draft-meta" },
      h("strong", { text: "Draft clip" }),
      h("span", { class: "small muted", text: `${d.fps} fps, draft quality, ${formatBytes(d.size || 0)}. Rendered in ${Math.round(d.seconds || 0)} s${d.at ? `, ${ago(d.at)}` : ""}.` }),
      h("span", { class: `chip${estimated ? " warn" : " good"}`, text: estimated ? "Silent, estimated timing" : d.timing === "voiced" ? "With voice" : "With the saved voice" }),
      h("p", { class: "help", text: "Motion and timing are right; the final render is sharper, 30 fps and fully voiced." })));
}

/** Mounts the plan panel into `host`. Shows day plans only (the workbench
 * owns test plans). Returns a dispose function. */
export function mountPlanPanel(host, { onJobStarted }) {
  const cleanup = [];
  const selected = new Set();
  const open = new Set(); // video ids with the look editor open
  let renderedKey = "";
  let busyVideo = null; // {id, tier|"rewrite"|"studio"} while a request is in flight

  const title = h("h2", { id: "plan-h" });
  const sub = h("p", { class: "small faint" });
  const switcher = h("div", { class: "plan-switch", role: "tablist", "aria-label": "Plans" });
  const list = h("div", { class: "pv-list" });
  const footer = h("div", { class: "plan-foot" });
  const section = h("section", { class: "section plan-section", "aria-labelledby": "plan-h", hidden: true },
    h("div", { class: "section-head split" }, h("div", {}, title, sub), switcher), list, footer);
  host.append(section);

  const plan = () => (state.plan && !isTestRef(state.plan.ref) ? state.plan : null);
  const jobFor = (id) => {
    const latest = state.runs.latest;
    if (!latest || latest.state !== "running" || !plan()) return null;
    if (latest.meta?.ref !== plan().ref) return null;
    if (latest.kind === "preview" || latest.kind === "rewrite") return latest.meta?.video === id ? latest : null;
    if (latest.kind === "render") return (!latest.meta?.only || latest.meta.only.includes(id)) ? latest : null;
    return null;
  };

  async function withBusy(v, tier, fn) {
    if (isRunning() || busyVideo) return;
    busyVideo = { id: v.id, tier };
    render();
    const ok = await attempt(fn);
    busyVideo = null;
    renderedKey = "";
    render();
    if (ok && tier !== "studio" && tier !== "look") onJobStarted?.();
  }

  function render() {
    const p = plan();
    section.hidden = !p;
    renderSwitcher();
    if (!p) return;
    const running = state.runs.latest?.state === "running" ? state.runs.latest : null;
    const key = JSON.stringify([p, [...selected], [...open], busyVideo, running?.id, running?.kind, running?.meta, state.characters.length, isRunning(), state.looks?.presets?.length]);
    if (key === renderedKey) return;
    renderedKey = key;
    title.textContent = p.date === todayIso() ? `Today's videos, ${formatDate(p.date)}` : `Plan for ${formatDate(p.date)}`;
    const rendered = p.videos.filter((v) => v.status === "rendered").length;
    sub.textContent = `${plural(p.videos.length, "video")} planned${rendered ? `, ${rendered} rendered` : ""}. Open a video to try another style, preview it, then render what you want.`;
    list.replaceChildren(...p.videos.map((v) => videoCard(v)));
    renderFooter();
  }

  function renderSwitcher() {
    const plans = (state.plans?.plans || []).filter((card) => !isTestRef(card.ref));
    switcher.replaceChildren();
    if (plans.length < 2) return;
    for (const card of plans.slice(0, 5)) {
      const on = state.planRef === card.ref;
      switcher.append(h("button", { class: `chip-btn${on ? " on" : ""}`, type: "button", role: "tab", "aria-selected": String(on),
        onClick: () => loadPlan(card.ref).then(() => { renderedKey = ""; render(); }).catch((e) => toast(e.message, "bad")) },
      formatDate(card.date), h("span", { class: "faint", text: ` · ${card.count}` })));
    }
  }

  // per video: the style being edited (kept across re-renders) and which tile's editor is open
  const edits = new Map();
  const defaultValue = (v) => valueFromLook(v.default_look || plan().default_look); // per video: it includes the colour its place in the day gives it
  const savedValue = (v) => valueFromLook(v.resolved);
  const editValue = (v) => edits.get(v.id) || savedValue(v);
  const isCustom = (v) => Object.keys(v.look || {}).length > 0;

  // the animation colour this video will get (Dark glass), so a day's alternating colours are visible at a glance
  function accentDot(v) {
    const r = v.resolved || {};
    if (r.style === "avatar" || !r.accent || r.theme !== "cinematic") return null;
    const rotating = !v.look || v.look.accent === undefined;
    return h("span", { class: "accent-dot", style: `--c:${r.accent}`, title: `Animation colour ${r.accent}${rotating ? " (from the default)" : " (set for this video)"}` },
      h("i", { "aria-hidden": "true" }), h("span", { class: "faint small", text: r.accent }));
  }

  function statusChip(v, prev) {
    if (v.status === "rendered") return h("span", { class: "chip good" }, icon("check"), v.rendered?.youtube ? "Uploaded" : "Rendered");
    if (v.status === "failed") return h("span", { class: "chip bad" }, icon("alert"), "Failed");
    if (prev.draft) return h("span", { class: "chip" }, icon("play", "sm"), prev.draft.trial ? "Trial drafted" : "Drafted");
    return h("span", { class: "chip", text: "Planned" });
  }

  function videoCard(v) {
    const p = plan();
    const job = jobFor(v.id);
    const busy = busyVideo?.id === v.id;
    const blocked = isRunning() || Boolean(busyVideo);
    const prev = v.preview || {};
    const custom = isCustom(v);
    const card = h("article", { class: `pv${v.status === "rendered" ? " done" : ""}`, "aria-label": v.title });

    const check = h("input", { type: "checkbox", "aria-label": `Select ${v.title} for rendering`, checked: selected.has(v.id),
      disabled: v.status === "rendered", onChange: () => { check.checked ? selected.add(v.id) : selected.delete(v.id); renderFooter(); } });
    const head = h("div", { class: "pv-head" },
      h("label", { class: "pv-pick" }, check, h("span", { class: "pv-num", text: String(v.index).padStart(2, "0") })),
      previewTile(savedValue(v), { small: true }),
      h("div", { class: "pv-title" },
        h("h3", { text: v.title }),
        h("div", { class: "pv-meta" }, h("span", { class: "chip", text: v.badge }), statusChip(v, prev),
          h("span", { class: `chip${custom ? " active" : ""}`, title: custom ? "This video has its own style" : "It follows the default style", text: custom ? "Custom style" : "Default style" }),
          accentDot(v),
          h("span", { class: "faint small", text: v.scenes ? `${v.scenes.length} scenes, ${v.words} words` : `${v.words} words` }),
          v.writer === "heuristic" ? h("span", { class: "chip warn", text: "Rule-based script" }) : null),
        h("p", { class: "pv-style small muted", text: describeValue(savedValue(v)) })));
    const stories = h("p", { class: "pv-stories small muted", text: v.stories.join(" · ") });

    const isOpen = open.has(v.id);
    const toggle = h("button", { class: "btn small", type: "button", "aria-expanded": String(isOpen), onClick: () => {
      if (isOpen) { open.delete(v.id); edits.delete(v.id); } else open.add(v.id);
      renderedKey = ""; render();
    } }, icon("sliders", "sm"), isOpen ? "Close style" : "Style for this video");
    const studio = p.studio && p.studio.ref === p.ref && p.studio.video === v.id ? p.studio : null;
    const studioBtn = studio
      ? h("span", { class: "pv-studio" },
        h("a", { class: "btn small", href: studio.url, target: "_blank", rel: "noopener" }, icon("link", "sm"), "Studio open"),
        h("button", { class: "btn small quiet", type: "button", onClick: () => attempt(() => stopJob(studio.id).then(() => loadPlan(p.ref))), title: "Stops the Studio server" }, icon("stop", "sm"), "Stop"))
      : h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => withBusy(v, "studio", () => openStudio(p.ref, v.id).then(() => loadPlan(p.ref))),
        title: "Opens the live composition in HyperFrames Studio (a new tab) to scrub and tweak it. Free; starting it for another video stops this one." },
      busy && busyVideo.tier === "studio" ? spinner() : icon("layers", "sm"), "Open in Studio");
    const rewriteBtn = h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => withBusy(v, "rewrite", () => rewriteVideo(p.ref, v)) },
      busy && busyVideo.tier === "rewrite" ? spinner() : icon("refresh", "sm"), "Rewrite script…");
    const quickDraft = isOpen ? null : h("button", { class: "btn small", type: "button", disabled: blocked,
      onClick: () => withBusy(v, "draft", () => startPreview(p.ref, v.id, "draft", false, { overrides: diffFromDefault(savedValue(v), defaultValue(v)), trial: true })) },
    busy && busyVideo.tier === "draft" ? spinner() : icon("play", "sm"), "Preview this video", costTag("free · 30–60 s"));
    const actions = h("div", { class: "pv-actions" }, toggle, quickDraft, studioBtn, h("span", { class: "grow" }), rewriteBtn);

    const progress = job ? h("div", { class: "pv-progress", role: "status" }, spinner(),
      h("span", {}, h("strong", { text: job.kind === "preview" ? "Rendering a draft clip (about 30–60 s, free)" : job.kind === "rewrite" ? "Rewriting the script" : "Rendering at full quality" }),
      h("span", { class: "faint", text: ` · ${job.stage || "starting"}` }))) : null;

    const editor = isOpen ? styleEditor(v, { blocked, busy }) : null;
    const previews = h("div", { class: "pv-previews" });
    const pair = draftPair(v);
    if (pair) previews.append(pair);
    append(card, [head, stories, actions, editor, progress, previews]);
    return card;
  }

  // The draft beside the default-style result, when both exist.
  function draftPair(v) {
    const prev = v.preview || {};
    if (!prev.draft) return null;
    // a trial is compared with the saved style's kept draft; a saved draft with the previous style's
    const left = (prev.looks || []).find((e) => e.draft && (prev.draft.trial ? e.key === v.look_key : e.key !== v.look_key));
    const right = h("figure", { class: "cmp" },
      h("video", { src: `${prev.draft.url}?t=${Math.round(prev.draft.at || 0)}`, controls: true, preload: "metadata", playsinline: true, "aria-label": "Draft with this style" }),
      h("figcaption", {}, h("strong", { text: prev.draft.trial ? "This style (trial, not saved)" : "Now" }),
        h("span", { class: "small faint", text: `${Math.round(prev.draft.seconds || 0)} s to render · ${prev.draft.timing === "estimated timing" ? "silent, estimated timing" : "with voice"}${prev.draft.at ? ` · ${ago(prev.draft.at)}` : ""}` })));
    if (!left) return h("div", { class: "cmp-one" }, right,
      h("p", { class: "help", text: prev.draft.trial ? "Motion and timing are right; the final render is sharper, 30 fps and fully voiced. Preview the saved style too to see the two side by side." : "Motion and timing are right; the final render is sharper, 30 fps and fully voiced. Change the style above and press Preview this video to see the two side by side." }));
    return h("div", { class: "cmp-grid" },
      h("figure", { class: "cmp" },
        h("video", { src: left.draft.url, controls: true, preload: "metadata", playsinline: true, "aria-label": "Draft with the earlier style" }),
        h("figcaption", {}, h("strong", { text: prev.draft.trial ? "Saved style" : "Before" }), h("span", { class: "small faint", text: `${describeValue(valueFromLook(left.look || {}))} · ${ago(left.draft.at || left.at)}` }))),
      right);
  }

  function styleEditor(v, { blocked, busy }) {
    const def = defaultValue(v);
    const saved = savedValue(v);
    const picker = stylePicker({ value: editValue(v), onChange: (val) => { edits.set(v.id, val); refreshButtons(); } });
    const diff = () => diffFromDefault(editValue(v), def);
    const preview = h("button", { class: "btn primary", type: "button", disabled: blocked, onClick: () => withBusy(v, "draft", () => startPreview(plan().ref, v.id, "draft", false, { overrides: diff(), trial: true })) },
      busy && busyVideo.tier === "draft" ? spinner() : icon("play", "sm"), "Preview this video", costTag("free · 30–60 s"));
    const use = h("button", { class: "btn", type: "button", disabled: blocked, onClick: () => withBusy(v, "look", async () => {
      await saveLook(plan().ref, v.id, diff());
      edits.delete(v.id);
      toast(Object.keys(diff()).length ? "This video will use that style. Nothing was rendered." : "This video follows the default style.");
    }) }, icon("check", "sm"), "Use this style for this video");
    const reset = h("button", { class: "btn quiet", type: "button", disabled: blocked || !isCustom(v) && !edits.has(v.id), onClick: () => withBusy(v, "look", async () => {
      await saveLook(plan().ref, v.id, {});
      edits.delete(v.id);
      toast("Back to the default style for this video.");
    }) }, "Reset to default");
    const state_ = h("p", { class: "help sp-state", "aria-live": "polite" });
    function refreshButtons() {
      const same = sameValue(editValue(v), saved);
      use.disabled = blocked || same;
      state_.textContent = same ? (isCustom(v) ? "This video has its own style. Change it, then preview or use it." : "This video follows the default style. Change anything below to try another.")
        : "Not saved yet: preview it to see how it looks, then use it if you like it.";
    }
    refreshButtons();
    return h("div", { class: "pv-editor sp-editor" },
      h("h4", { text: "Style for this video" }), picker,
      h("div", { class: "form-actions" }, preview, use, reset),
      state_);
  }

  function renderFooter() {
    const p = plan();
    footer.replaceChildren();
    if (!p) return;
    const pending = p.videos.filter((v) => v.status !== "rendered").length;
    const n = selected.size;
    const blocked = isRunning();
    const s = state.settings.values;
    footer.append(
      h("div", { class: "plan-foot-text" },
        h("strong", { text: "Final render" }),
        h("span", { class: "small muted", text: pending ? `Full quality and voiced, about ${approxRender(1)} per video.${s.YOUTUBE_UPLOAD === "1" ? ` Uploads as ${s.YOUTUBE_PRIVACY}.` : " Not uploaded."}` : "Everything in this plan is rendered." })),
      h("span", { class: "grow" }),
      h("button", { class: "btn small", type: "button", disabled: blocked || !n, onClick: () => attempt(async () => { if (await renderFinal(p, [...selected])) { selected.clear(); onJobStarted?.(); } }) }, n ? `Render selected (${n})…` : "Render selected…"),
      h("button", { class: "btn small primary", type: "button", disabled: blocked || !pending, onClick: () => attempt(async () => { if (await renderFinal(p, null)) onJobStarted?.(); }) }, icon("film", "sm"), "Render all…"));
  }

  cleanup.push(subscribe(render));
  render();
  return () => { cleanup.forEach((fn) => fn()); section.remove(); };
}

const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

export function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "long", day: "numeric" });
}
