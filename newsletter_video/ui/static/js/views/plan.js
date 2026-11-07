// The plan panel: the day's (or a test's) planned videos, each with its
// look, its previews and the explicit actions that cost time or money.
import { api } from "../api.js";
import { h, append, icon, spinner, formatDuration, formatBytes, plural } from "../dom.js";
import { toast } from "../components.js";
import { state, subscribe, loadPlan, refreshRuns, isRunning } from "../store.js";

export function confirmDialog({ title, body, confirmLabel, tone = "primary" }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { "aria-labelledby": "dlg-title" },
      h("form", { method: "dialog" },
        h("h2", { id: "dlg-title", text: title }), body,
        h("div", { class: "actions" },
          h("button", { class: "btn quiet", value: "cancel", type: "submit" }, "Cancel"),
          h("button", { class: `btn ${tone}`, value: "ok", type: "submit" }, confirmLabel))));
    dialog.addEventListener("close", () => { resolve(dialog.returnValue === "ok"); dialog.remove(); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

const STYLE_LABEL = { avatar: "Avatar", explainer: "Explainer" };
const THEME_LABEL = { light: "Light", dark: "Dark" };
const BG_LABEL = { gameplay: "Gameplay", none: "Plain" };

export function recipeLabel(id, recipes) {
  if (!id || id === "auto") return "Planner picks recipes";
  if (id === "none") return "No recipes";
  const found = (recipes || []).find((r) => r.id === id);
  return found ? found.name : id;
}

function characterName(slug) {
  if (!slug) return "Default narrator";
  const c = state.characters.find((x) => x.slug === slug);
  return c ? c.name : slug;
}

export function lookChips(resolved, overrides, recipes) {
  const set = (k) => overrides && overrides[k] !== undefined;
  const chip = (text, k) => h("span", { class: `chip${set(k) ? " active" : ""}`, title: set(k) ? "Set for this video" : "From your defaults" }, text);
  const chips = [chip(`${STYLE_LABEL[resolved.style] || resolved.style} style`, "style"), chip(characterName(resolved.character), "character")];
  if (resolved.style === "avatar") chips.push(chip(`${BG_LABEL[resolved.background] || resolved.background} background`, "background"));
  else {
    chips.push(chip(`${THEME_LABEL[resolved.theme] || resolved.theme} theme`, "theme"));
    chips.push(chip(recipeLabel(resolved.recipe, recipes), "recipe"));
  }
  return chips;
}

/** Mounts the plan panel into `host`. Returns a dispose function. */
export function mountPlanPanel(host, { onJobStarted }) {
  const cleanup = [];
  const selected = new Set();
  const open = new Set(); // video ids with the look editor open
  let renderedKey = "";
  let busyVideo = null; // {id, tier|"rewrite"} while a request is in flight

  const title = h("h2", { id: "plan-h" });
  const sub = h("p", { class: "small faint" });
  const switcher = h("div", { class: "plan-switch", role: "tablist", "aria-label": "Plans" });
  const list = h("div", { class: "pv-list" });
  const footer = h("div", { class: "plan-foot" });
  const section = h("section", { class: "section plan-section", "aria-labelledby": "plan-h", hidden: true },
    h("div", { class: "section-head split" }, h("div", {}, title, sub), switcher), list, footer);
  host.append(section);

  // ---- data helpers ----
  const recipes = () => state.plans?.recipes || [];
  const plan = () => state.plan;
  const jobFor = (id) => {
    const latest = state.runs.latest;
    if (!latest || latest.state !== "running" || !plan()) return null;
    if (latest.meta?.ref !== plan().ref) return null;
    if (latest.kind === "preview" || latest.kind === "rewrite") return latest.meta?.video === id ? latest : null;
    if (latest.kind === "render") return (!latest.meta?.only || latest.meta.only.includes(id)) ? latest : null;
    return null;
  };

  // ---- actions ----
  async function startPreview(v, tier, voice = false) {
    if (isRunning() || busyVideo) return;
    busyVideo = { id: v.id, tier };
    render();
    try {
      await api.post(`/api/plans/${encodeURIComponent(plan().ref)}/videos/${encodeURIComponent(v.id)}/preview`, { tier, voice });
      await refreshRuns();
      onJobStarted?.();
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      busyVideo = null;
      render();
    }
  }

  // HyperFrames Studio: the live composition (scrub, tweak) on a local port.
  // The tab is opened on the click itself so the browser doesn't block it,
  // then pointed at the server once it answers.
  async function openStudio(v) {
    if (busyVideo) return;
    const tab = window.open("about:blank", "_blank");
    busyVideo = { id: v.id, tier: "studio" };
    render();
    try {
      const data = await api.post(`/api/plans/${encodeURIComponent(plan().ref)}/videos/${encodeURIComponent(v.id)}/studio`, {});
      if (tab) tab.location = data.url; else window.open(data.url, "_blank");
      if (!data.ready) toast("Studio is still starting; reload its tab in a few seconds.", "warn");
      await loadPlan(plan().ref);
    } catch (err) {
      if (tab) tab.close();
      toast(err.message, "bad");
    } finally {
      busyVideo = null;
      renderedKey = "";
      render();
    }
  }

  async function stopStudio(studio) {
    try {
      await api.post(`/api/jobs/${encodeURIComponent(studio.id)}/cancel`, {});
      await loadPlan(plan().ref);
    } catch (err) {
      toast(err.message, "bad");
    }
    renderedKey = "";
    render();
  }

  async function rewrite(v) {
    if (isRunning() || busyVideo) return;
    const ok = await confirmDialog({
      title: "Rewrite this script?",
      body: h("div", { class: "stack" },
        h("p", { class: "muted", text: `The writer rewrites "${v.title}" from the same stories. The new script replaces this one; previews start over.` }),
        h("p", { class: "help", text: "This is a paid writer call, about 3 cents. Changing the look or previewing never costs anything." })),
      confirmLabel: "Rewrite script",
    });
    if (!ok) return;
    busyVideo = { id: v.id, tier: "rewrite" };
    render();
    try {
      await api.post(`/api/plans/${encodeURIComponent(plan().ref)}/videos/${encodeURIComponent(v.id)}/rewrite`, { confirm: true });
      await refreshRuns();
      onJobStarted?.();
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      busyVideo = null;
      render();
    }
  }

  async function renderFinal(ids) {
    if (isRunning()) return;
    const p = plan();
    const s = state.settings.values;
    const uploads = s.YOUTUBE_UPLOAD === "1" && !p.is_test;
    const n = ids ? ids.length : p.videos.filter((v) => v.status !== "rendered").length;
    if (!n) { toast("Every selected video is already rendered.", "bad"); return; }
    const ok = await confirmDialog({
      title: ids ? `Render ${plural(n, "video")} at full quality?` : "Render every planned video at full quality?",
      body: h("div", { class: "stack" },
        h("ul", {},
          h("li", { text: `Voices and renders ${plural(n, "video")} with the look set on each one. About ${approx(n)}.` }),
          h("li", { text: p.is_test ? "A test plan: never uploaded, never counts as a covered story."
            : uploads ? `Uploads them to YouTube as ${s.YOUTUBE_PRIVACY}, like the daily run.` : "Doesn't upload them (uploading is off in Settings)." }),
          h("li", { text: "Previews stay as they are. This is the only step that makes real videos." })),
        uploads ? h("p", { class: "help", text: "This is the real thing: it posts to your channel." }) : null),
      confirmLabel: ids ? "Render selected" : "Render all",
      tone: uploads ? "danger" : "primary",
    });
    if (!ok) return;
    try {
      await api.post(`/api/plans/${encodeURIComponent(p.ref)}/render`, { confirm: true, only: ids || null });
      selected.clear();
      await refreshRuns();
      onJobStarted?.();
    } catch (err) {
      toast(err.message, "bad");
    }
  }

  async function saveLook(v, look) {
    try {
      const data = await api.put(`/api/plans/${encodeURIComponent(plan().ref)}/videos/${encodeURIComponent(v.id)}/look`, { look });
      state.plan = data;
      open.delete(v.id);
      renderedKey = "";
      render();
      toast("Look saved for this video. Preview it to see the change; nothing was rendered.");
    } catch (err) {
      toast(err.message, "bad");
    }
  }

  // ---- rendering ----
  function render() {
    const p = plan();
    section.hidden = !p;
    renderSwitcher();
    if (!p) return;
    const running = state.runs.latest?.state === "running" ? state.runs.latest : null;
    const key = JSON.stringify([p, [...selected], [...open], busyVideo, running?.id, running?.kind, running?.meta, state.characters.length, isRunning()]);
    if (key === renderedKey) return;
    renderedKey = key;
    title.textContent = p.is_test ? "Test plan" : `Plan for ${formatDate(p.date)}`;
    const rendered = p.videos.filter((v) => v.status === "rendered").length;
    sub.textContent = `${plural(p.videos.length, "video")} planned${rendered ? `, ${rendered} rendered` : ""}. ` +
      (p.is_test ? "A throwaway plan: nothing here is uploaded or counted." : "Review each one, change its look, preview it, then render what you want.");
    list.replaceChildren(...p.videos.map((v) => videoCard(v)));
    renderFooter();
  }

  function renderSwitcher() {
    const plans = state.plans?.plans || [];
    switcher.replaceChildren();
    if (plans.length < 2 && !(plans.length === 1 && !state.plan)) return;
    for (const card of plans.slice(0, 5)) {
      const on = state.planRef === card.ref;
      switcher.append(h("button", { class: `chip-btn${on ? " on" : ""}`, type: "button", role: "tab", "aria-selected": String(on),
        onClick: () => loadPlan(card.ref).then(() => { renderedKey = ""; render(); }).catch((e) => toast(e.message, "bad")) },
      card.kind === "test" ? "Test" : formatDate(card.date), h("span", { class: "faint", text: ` · ${card.count}` })));
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
      : v.status === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Failed")
        : null;
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
      h("span", { class: "label small", text: "Look" }), ...lookChips(v.resolved, v.look, recipes()),
      h("button", { class: "btn small quiet", type: "button", "aria-expanded": String(open.has(v.id)), onClick: () => {
        open.has(v.id) ? open.delete(v.id) : open.add(v.id); renderedKey = ""; render();
      } }, icon("sliders", "sm"), open.has(v.id) ? "Close" : "Change look"));
    const editor = open.has(v.id) ? lookEditor(v) : null;

    const stillsBtn = h("button", { class: "btn small primary", type: "button", disabled: blocked, onClick: () => startPreview(v, "stills") },
      busy && busyVideo.tier === "stills" ? spinner() : icon("image", "sm"), "Preview");
    const draftBtn = h("button", { class: "btn small", type: "button", disabled: blocked, onClick: () => startPreview(v, "draft") },
      busy && busyVideo.tier === "draft" ? spinner() : icon("play", "sm"), "Draft clip");
    const voiceBtn = h("button", { class: "btn small quiet", type: "button", disabled: blocked, title: "Voices the lines first (uses your narrator's voice, may take a minute), then renders a draft clip with real timing",
      onClick: () => startPreview(v, "draft", true) }, icon("voice", "sm"), "Draft with voice");
    const rewriteBtn = h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => rewrite(v) },
      busy && busyVideo.tier === "rewrite" ? spinner() : icon("refresh", "sm"), "Rewrite script…");
    const studio = p.studio && p.studio.ref === p.ref && p.studio.video === v.id ? p.studio : null;
    const studioBtn = studio
      ? h("span", { class: "pv-studio" },
        h("a", { class: "btn small", href: studio.url, target: "_blank", rel: "noopener" }, icon("link", "sm"), "Studio open"),
        h("button", { class: "btn small quiet", type: "button", onClick: () => stopStudio(studio), title: "Stops the Studio server" }, icon("stop", "sm"), "Stop"))
      : h("button", { class: "btn small quiet", type: "button", disabled: blocked, onClick: () => openStudio(v),
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
      previews.append(h("p", { class: "help", text: "No preview yet. Preview captures one frame per scene in about 15 seconds; Draft clip renders the whole video small and fast. Neither costs anything." }));
    }
    append(card, [head, stories, scenes, lookRow, editor, actions, progress, previews]);
    return card;
  }

  function stillsBlock(s) {
    const estimated = s.timing === "estimated timing";
    return h("figure", { class: "sb" },
      h("a", { href: s.url, target: "_blank", rel: "noopener", "aria-label": "Open the storyboard full size" },
        h("img", { src: `${s.url}?t=${Math.round(s.at || 0)}`, alt: "Storyboard: one frame per scene", loading: "lazy" })),
      h("figcaption", { class: "sb-cap" },
        h("span", {}, h("strong", { text: "Storyboard" }), ` · ${plural((s.frames || []).length, "frame")} · ready in ${Math.round(s.seconds || 0)} s`),
        h("span", { class: `chip${estimated ? " warn" : " good"}`, text: estimated ? "Estimated timing" : s.timing === "voiced" ? "Voiced" : "Real timing from the saved voice" }),
        h("span", { class: "faint", text: `about ${formatDuration(s.duration || 0)} long` })));
  }

  function draftBlock(d) {
    const estimated = d.timing === "estimated timing";
    return h("div", { class: "draft" },
      h("video", { src: `${d.url}?t=${Math.round(d.at || 0)}`, controls: true, preload: "metadata", playsinline: true, "aria-label": "Draft clip" }),
      h("div", { class: "draft-meta" },
        h("strong", { text: "Draft clip" }),
        h("span", { class: "small muted", text: `${d.fps} fps, draft quality, ${formatBytes(d.size || 0)}. Rendered in ${Math.round(d.seconds || 0)} s.` }),
        h("span", { class: `chip${estimated ? " warn" : " good"}`, text: estimated ? "Silent, estimated timing" : d.timing === "voiced" ? "With voice" : "With the saved voice" }),
        h("p", { class: "help", text: "Motion and timing are right; the final render is sharper, 30 fps and fully voiced." })));
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
    const themeSeg = seg("Theme", [["light", "Light"], ["dark", "Dark"]], "theme", `Default (${THEME_LABEL[d.theme]})`);
    const bgSeg = seg("Background", [["gameplay", "Gameplay"], ["none", "Plain"]], "background", `Default (${BG_LABEL[d.background]})`);
    const recipeSel = h("select", { class: "select", "aria-label": "Style recipe", onChange: () => {
      const val = recipeSel.value; if (val === "\u0000") delete draft.recipe; else draft.recipe = val;
    } }, h("option", { value: "\u0000", text: `Default (${recipeLabel(d.recipe, recipes())})` }),
    h("option", { value: "auto", text: "Auto (planner picks)" }), h("option", { value: "none", text: "None (template look)" }),
    ...recipes().map((r) => h("option", { value: r.id, text: `${r.name}${r.fidelity != null ? ` · fidelity ${r.fidelity.toFixed(2)}` : ""}` })));
    recipeSel.value = draft.recipe !== undefined ? draft.recipe : "\u0000";
    const themeRow = row("Theme", themeSeg, "Explainer videos only.");
    const bgRow = row("Background", bgSeg, "Avatar videos only.");
    const recipeRow = row("Style recipe", recipeSel, recipes().length ? "From your style library. Changing it re-styles the scenes without a writer call."
      : "No style library yet. Add references on the Styles page.");
    function refreshVisibility() {
      const style = draft.style || d.style;
      themeRow.hidden = style !== "explainer";
      recipeRow.hidden = style !== "explainer";
      bgRow.hidden = style !== "avatar";
    }
    refreshVisibility();
    return h("div", { class: "pv-editor" },
      row("Style", styleSeg, "Avatar shows the narrator over footage; Explainer is clean graphics."),
      row("Narrator", charSel, "Their voice comes with them."),
      themeRow, bgRow, recipeRow,
      h("div", { class: "form-actions" },
        h("button", { class: "btn small primary", type: "button", onClick: () => saveLook(v, draft) }, "Save look"),
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
    footer.append(
      h("div", { class: "plan-foot-text" },
        h("strong", { text: "Final render" }),
        h("span", { class: "small muted", text: pending ? `Full quality and voiced, about 5 minutes per video.${p.is_test ? " Never uploaded." : state.settings.values.YOUTUBE_UPLOAD === "1" ? ` Uploads as ${state.settings.values.YOUTUBE_PRIVACY}.` : " Not uploaded."}` : "Everything in this plan is rendered." })),
      h("span", { class: "grow" }),
      h("button", { class: "btn small", type: "button", disabled: blocked || !n, onClick: () => renderFinal([...selected]) }, n ? `Render selected (${n})…` : "Render selected…"),
      h("button", { class: "btn small primary", type: "button", disabled: blocked || !pending, onClick: () => renderFinal(null) }, icon("film", "sm"), "Render all…"));
  }

  cleanup.push(subscribe(render));
  render();
  return () => { cleanup.forEach((fn) => fn()); section.remove(); };
}

function approx(n) {
  const minutes = n * 5;
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours > 1 ? "s" : ""}${minutes % 60 ? ` ${minutes % 60} minutes` : ""}`;
}

export function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "long", day: "numeric" });
}
