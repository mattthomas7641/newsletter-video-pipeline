// The plan panel: a day's planned videos, each with its look, its previews
// and the explicit actions that cost time or money. The look vocabulary
// (chips, labels, preview blocks) is shared with the workbench.
import { h, append, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { toast, ago } from "../components.js";
import { state, subscribe, loadPlan, isRunning, isTestRef } from "../store.js";
import { startPreview, saveLook, openStudio, stopJob, rewriteVideo, renderFinal, attempt, approxRender } from "../actions.js";
import { presetName, presetSelect } from "../presets.js";

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
    title.textContent = `Plan for ${formatDate(p.date)}`;
    const rendered = p.videos.filter((v) => v.status === "rendered").length;
    sub.textContent = `${plural(p.videos.length, "video")} planned${rendered ? `, ${rendered} rendered` : ""}. Change a look, preview it, then render what you want.`;
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

  function videoCard(v) {
    const p = plan();
    const job = jobFor(v.id);
    const busy = busyVideo?.id === v.id;
    const blocked = isRunning() || Boolean(busyVideo);
    const prev = v.preview || {};
    const card = h("article", { class: `pv${v.status === "rendered" ? " done" : ""}`, "aria-label": v.title });

    const check = h("input", { type: "checkbox", "aria-label": `Select ${v.title} for rendering`, checked: selected.has(v.id),
      disabled: v.status === "rendered", onChange: () => { check.checked ? selected.add(v.id) : selected.delete(v.id); renderFooter(); } });
    const status = v.status === "rendered" ? h("span", { class: "chip good" }, icon("check"), "Rendered")
      : v.status === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Failed") : null;
    const head = h("div", { class: "pv-head" },
      h("label", { class: "pv-pick" }, check, h("span", { class: "pv-num", text: String(v.index).padStart(2, "0") })),
      h("div", { class: "pv-title" },
        h("h3", { text: v.title }),
        h("div", { class: "pv-meta" }, h("span", { class: "chip", text: v.badge }), status,
          h("span", { class: "faint small", text: v.scenes ? `${v.scenes.length} scenes, ${v.words} words` : `${v.words} words` }),
          v.writer === "heuristic" ? h("span", { class: "chip warn", text: "Rule-based script" }) : null)));
    const stories = h("p", { class: "pv-stories small muted", text: v.stories.join(" · ") });
    const scenes = v.scenes ? h("ol", { class: "pv-scenes", "aria-label": "Scenes" }, v.scenes.map((t) => h("li", { text: t }))) : null;

    const lookRow = h("div", { class: "pv-look" },
      h("span", { class: "label small", text: "Look" }), ...lookChips(v.resolved, v.look),
      h("button", { class: "btn small quiet", type: "button", "aria-expanded": String(open.has(v.id)), onClick: () => {
        open.has(v.id) ? open.delete(v.id) : open.add(v.id); renderedKey = ""; render();
      } }, icon("sliders", "sm"), open.has(v.id) ? "Close" : "Change look"));
    const editor = open.has(v.id) ? lookEditor(v) : null;

    const stillsBtn = h("button", { class: "btn small primary", type: "button", disabled: blocked, onClick: () => withBusy(v, "stills", () => startPreview(p.ref, v.id, "stills")) },
      busy && busyVideo.tier === "stills" ? spinner() : icon("image", "sm"), "Storyboard");
    const draftBtn = h("button", { class: "btn small", type: "button", disabled: blocked, onClick: () => withBusy(v, "draft", () => startPreview(p.ref, v.id, "draft")) },
      busy && busyVideo.tier === "draft" ? spinner() : icon("play", "sm"), "Draft clip");
    const voiceBtn = h("button", { class: "btn small quiet", type: "button", disabled: blocked, title: "Voices the lines first (uses your narrator's voice, may take a minute), then renders a draft clip with real timing",
      onClick: () => withBusy(v, "voice", () => startPreview(p.ref, v.id, "draft", true)) }, icon("voice", "sm"), "Draft with voice");
    const rewriteBtn = h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => withBusy(v, "rewrite", () => rewriteVideo(p.ref, v)) },
      busy && busyVideo.tier === "rewrite" ? spinner() : icon("refresh", "sm"), "Rewrite script…");
    const studio = p.studio && p.studio.ref === p.ref && p.studio.video === v.id ? p.studio : null;
    const studioBtn = studio
      ? h("span", { class: "pv-studio" },
        h("a", { class: "btn small", href: studio.url, target: "_blank", rel: "noopener" }, icon("link", "sm"), "Studio open"),
        h("button", { class: "btn small quiet", type: "button", onClick: () => attempt(() => stopJob(studio.id).then(() => loadPlan(p.ref))), title: "Stops the Studio server" }, icon("stop", "sm"), "Stop"))
      : h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => withBusy(v, "studio", () => openStudio(p.ref, v.id).then(() => loadPlan(p.ref))),
        title: "Opens the live composition in HyperFrames Studio (a new tab) to scrub and tweak it. Free; starting it for another video stops this one." },
      busy && busyVideo.tier === "studio" ? spinner() : icon("layers", "sm"), "Open in Studio");
    const actions = h("div", { class: "pv-actions" }, stillsBtn, draftBtn, voiceBtn, studioBtn, h("span", { class: "grow" }), rewriteBtn);

    const progress = job ? h("div", { class: "pv-progress", role: "status" }, spinner(),
      h("span", {}, h("strong", { text: job.kind === "preview" ? (job.meta.tier === "draft" ? "Rendering a draft clip" : "Capturing the storyboard")
        : job.kind === "rewrite" ? "Rewriting the script" : "Rendering at full quality" }),
      h("span", { class: "faint", text: ` · ${job.stage || "starting"}` }))) : null;

    const previews = h("div", { class: "pv-previews" });
    if (prev.stills) previews.append(stillsBlock(prev.stills));
    if (prev.draft) previews.append(draftBlock(prev.draft));
    if (!prev.stills && !prev.draft && !job) {
      previews.append(h("p", { class: "help", text: "No preview yet. Storyboard captures one frame per scene in about 15 seconds; Draft clip renders the whole video small and fast. Neither costs anything." }));
    }
    append(card, [head, stories, scenes, lookRow, editor, actions, progress, previews]);
    return card;
  }

  function lookEditor(v) {
    const d = state.plan.default_look;
    const draft = { ...v.look };
    const seg = (name, options, key, defaultLabel) => h("div", { class: "seg", role: "radiogroup", "aria-label": name },
      [["", defaultLabel], ...options].map(([val, label]) => h("label", {},
        h("input", { type: "radio", name: `${v.id}-${key}`, value: val, checked: (draft[key] ?? "") === val,
          onChange: () => { if (val === "") delete draft[key]; else draft[key] = val; refreshVisibility(); } }),
        h("span", { class: "opt", text: label }))));
    const styleSeg = seg("Style", [["avatar", "Avatar"], ["explainer", "Explainer"]], "style", `Default (${STYLE_LABEL[d.style]})`);
    const charSel = h("select", { class: "select", "aria-label": "Narrator", onChange: () => {
      const val = charSel.value; if (val === "\u0000") delete draft.character; else draft.character = val;
    } }, h("option", { value: "\u0000", text: `Default (${characterName(d.character)})` }),
    ...state.characters.map((c) => h("option", { value: c.slug, text: c.slug ? c.name : "Default narrator" })));
    charSel.value = draft.character !== undefined ? draft.character : "\u0000";
    const themeSeg = seg("Theme", [["light", "Light"], ["dark", "Dark"], ["cinematic", "Cinematic"]], "theme", `Default (${THEME_LABEL[d.theme]})`);
    const bgSeg = seg("Background", [["gameplay", "Gameplay"], ["none", "Plain"]], "background", `Default (${BG_LABEL[d.background]})`);
    const presetPick = presetSelect({
      value: draft.preset ?? "", defaultLabel: `Default look (${presetName(d.preset)})`,
      onChange: (val) => { if (val === "") delete draft.preset; else draft.preset = val; delete draft.theme; refreshVisibility(); },
    });
    const presetRow = row("Look preset", presetPick, "From the look library. Sets the theme and the motion of the scenes without a writer call; try them side by side in the Look workbench.");
    const themeRow = row("Theme", themeSeg, "Explainer videos only. A preset brings its own theme; pick one here to override it.");
    const bgRow = row("Background", bgSeg, "Avatar videos only.");
    function refreshVisibility() {
      const style = draft.style || d.style;
      themeRow.hidden = style !== "explainer";
      presetRow.hidden = style !== "explainer";
      bgRow.hidden = style !== "avatar";
      for (const input of themeSeg.querySelectorAll("input")) input.checked = (draft.theme ?? "") === input.value;
    }
    refreshVisibility();
    return h("div", { class: "pv-editor" },
      row("Style", styleSeg, "Avatar shows the narrator over footage; Explainer is clean graphics."),
      presetRow,
      row("Narrator", charSel, "Their voice comes with them."),
      themeRow, bgRow,
      h("div", { class: "form-actions" },
        h("button", { class: "btn small primary", type: "button", onClick: () => withBusy(v, "look", async () => {
          await saveLook(plan().ref, v.id, draft);
          open.delete(v.id);
          toast("Look saved for this video. Preview it to see the change; nothing was rendered.");
        }) }, "Save look"),
        h("button", { class: "btn small quiet", type: "button", onClick: () => { open.delete(v.id); renderedKey = ""; render(); } }, "Cancel"),
        h("span", { class: "help", text: "\"Default\" follows your saved settings, so changing the defaults later changes this video too." })));
  }

  function row(label, control, help) {
    return h("div", { class: "pv-row" }, h("div", {}, h("span", { class: "label", text: label }), h("p", { class: "help", text: help })), h("div", { class: "control" }, control));
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

export function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "long", day: "numeric" });
}
