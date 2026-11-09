// The Design lab: for trying new designs, or seeing how a render looks without
// drafting a video. Not part of the daily routine (that is the Lineup page).
// One guided flow in three stages.
//   1. Look library: the presets, one card each. Picking one makes it the look
//      of the test video (or of the test video about to be written).
//   2. Test video: write one throwaway video from today's newsletter in that look.
//   3. Tweak and compare: the storyboard in the current look beside the same
//      story in another look (an earlier capture of this video), scene by scene;
//      every look change re-captures the storyboard for free. Rewrite and
//      final render are explicit, confirmed actions.
import { h, append, icon, spinner, formatDuration, plural } from "../dom.js";
import { toast, costTag, ago } from "../components.js";
import { state, subscribe, isRunning, runningJob, loadPlans, loadPlan, setBench, isTestRef,
  presets, presetInfo, effectivePresetId } from "../store.js";
import { planTest, startPreview, saveLook, openStudio, stopJob, rewriteVideo, renderFinal, attempt, approxRender, WRITER_COST } from "../actions.js";
import { presetName, presetThumb, presetMark, presetGrid, presetSelect } from "../presets.js";
import { mountRunPanel } from "./run.js";
import { lookLabel, characterName, draftBlock } from "./plan.js";
import { mountDayPlan } from "./dayplan.js";

export function mountWorkbench(root) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);
  let makeOpen = false;
  let busy = null; // "plan" | "look" | "stills" | "draft" | "voice" | "studio" | "rewrite" | "render" while a request is in flight
  let renderedKey = "";
  let pendingTestJob = null; // the plan job we started: its plan becomes the current test when it finishes
  let compareKey = null; // the kept look shown on the left of the compare view (null: the most recent one)
  const make = { character: undefined }; // the narrator for the next test video (the look comes from stage 1)

  // ---- header + rail ----
  const ledger = h("p", { class: "ledger", "aria-live": "polite" });
  const head = h("header", { class: "page-head" },
    h("div", {}, h("h1", { text: "Design lab" }),
      h("p", { class: "lede" }, "For trying new designs, or seeing how a render looks without drafting a video: pick a look, write one throwaway test video, then compare looks on the same story with storyboards. Your daily videos are on the ", h("a", { class: "link", href: "#/", text: "Lineup page" }), ". Only Render makes a real video.")),
    ledger);
  const rail = h("ol", { class: "rail", "aria-label": "Where you are" });
  const next = h("p", { class: "rail-next" });
  const runHost = h("div");

  // ---- stage 1: the look library ----
  const librarySub = h("p", { class: "stage-sub" });
  const libraryHost = h("div");
  const stage1 = stage(1, "Look library", librarySub, null, libraryHost);

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

  // Plan review (approval mode) and the rendered videos: not the everyday page any more, kept working here.
  const dayHost = h("div", { class: "day-plan" });
  const dayDetails = h("details", { class: "panel day-details" },
    h("summary", {}, h("strong", { text: "Plan review and rendered videos" }), h("span", { class: "faint small", text: " · for DAILY_MODE=plan (approval mode), planning by hand, and watching what was made" })), dayHost);
  root.append(head, rail, next, runHost, stage1, stage2, stage3, dayDetails);
  cleanup.push(mountDayPlan(dayHost));
  // jobs that belong to a test plan; once the plan list is known, a finished
  // job for a plan that no longer exists has nothing to show
  const runPanel = mountRunPanel(runHost, { accept: (job) => (Boolean(job.meta?.test) || isTestRef(job.meta?.ref))
    && (job.state === "running" || job.state === "failed" || job.state === "interrupted" || !state.plans || !job.meta?.ref || testCards().some((p) => p.ref === job.meta.ref)) });
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
  const testCards = () => (state.plans?.plans || []).filter((p) => isTestRef(p.ref));
  const plan = () => (state.plan && isTestRef(state.plan.ref) ? state.plan : null);
  const video = () => plan()?.videos?.[0] || null;
  // the look on the test video, else the one picked in the library, else the default
  const currentPresetId = () => { const v = video(); return effectivePresetId(v ? v.resolved.preset : state.bench.preset || plan()?.default_look?.preset || state.settings?.values?.LOOK_PRESET); };
  const jobFor = () => {
    const job = runningJob();
    const p = plan();
    return job && p && job.meta?.ref === p.ref ? job : null;
  };
  const blocked = () => isRunning() || Boolean(busy);
  const kept = () => { const v = video(); return (v?.preview?.looks || []).filter((e) => e.key !== v.look_key && e.url); };
  const compareWith = () => { const list = kept(); return list.find((e) => e.key === compareKey) || list[0] || null; };

  // ---- actions ----
  async function selectPreset(id) {
    if (blocked()) return;
    setBench({ preset: id });
    if (video()) {
      await applyLook({ preset: id });
      return;
    }
    renderedKey = "";
    render();
  }

  async function makeTest() {
    if (blocked()) return;
    const overrides = { preset: currentPresetId(), style: "explainer" };
    if (make.character !== undefined && make.character !== "\u0000") overrides.character = make.character;
    busy = "plan";
    render();
    await attempt(async () => {
      await planTest(overrides);
      toast(`Writing your test video in ${presetName(overrides.preset)}. About 30 seconds; the storyboard follows by itself.`, "good", 8000);
      pendingTestJob = state.runs.latest?.id || null;
      makeOpen = false;
      runPanel.focus();
    });
    busy = null;
    renderedKey = "";
    render();
  }

  // Saves a change to the test video's look and re-captures its storyboard
  // (free). A new preset brings its own theme, so an earlier hand-picked
  // theme is dropped with it; the storyboard being replaced is kept by the
  // server for the compare view.
  async function applyLook(patch) {
    if (blocked()) return;
    const p = plan(); const v = video();
    const look = { ...v.look };
    if ("preset" in patch) { delete look.theme; delete look.recipe; }
    for (const [k, val] of Object.entries(patch)) { if (val === "" || val == null) delete look[k]; else look[k] = val; }
    busy = "look";
    render();
    await attempt(async () => {
      await saveLook(p.ref, v.id, look);
      await startPreview(p.ref, v.id, "stills");
    });
    busy = null;
    compareKey = null; // the look just replaced is the natural thing to compare with
    renderedKey = "";
    render();
  }

  function nextLook() {
    const ids = presets().map((p) => p.id);
    if (ids.length < 2) return;
    const i = ids.indexOf(currentPresetId());
    const id = ids[(i + 1) % ids.length];
    setBench({ preset: id });
    applyLook({ preset: id });
  }

  async function preview(tier, voice = false) {
    if (blocked()) return;
    const p = plan(); const v = video();
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

  // ---- stage 1 rendering ----
  function renderLibrary() {
    libraryHost.replaceChildren();
    const list = presets();
    const v = video();
    if (!state.looks) {
      libraryHost.append(h("div", { class: "look-grid" }, [1, 2, 3].map(() => h("div", { class: "look-card sk skeleton" }))));
      return;
    }
    if (!list.length) {
      librarySub.textContent = "";
      libraryHost.append(h("div", { class: "panel empty" }, h("h3", { text: "The look library is empty" }),
        h("p", { text: "newsletter_video/looks/presets.json has no presets. Add one there and reload; the test video uses the template's own look meanwhile." })));
      return;
    }
    const current = currentPresetId();
    librarySub.textContent = v
      ? `${plural(list.length, "look")}. Pick one and the test video below re-captures in it (free, about 15 s).`
      : `${plural(list.length, "look")}, each a theme plus the motion of its scenes. Pick one to write the test video in; you can switch later for free.`;
    const defaultId = effectivePresetId(plan()?.default_look?.preset || state.settings?.values?.LOOK_PRESET);
    libraryHost.append(presetGrid({
      value: current, onSelect: selectPreset, disabled: blocked(),
      badges: (p) => [
        p.id === defaultId ? h("span", { class: "chip", text: "Your default" }) : null,
        v && p.id === current ? h("span", { class: "chip active" }, icon("check"), "On the test video") : null,
      ],
    }));
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
          onClick: () => { setBench({ planRef: card.ref }); compareKey = null; loadPlan(card.ref).then(() => { renderedKey = ""; render(); }).catch((e) => toast(e.message, "bad")); } },
        (card.titles?.[0] || "Test").slice(0, 32), h("span", { class: "faint", text: card.created ? ` · ${ago(Date.parse(card.created) / 1000)}` : "" })));
      }
    }
    testSub.textContent = v ? "A throwaway video from today's newsletter. Never uploaded, never counted as a covered story."
      : "One throwaway video written from today's newsletter in the look you picked. Never uploaded.";

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
    const preset = presetInfo(currentPresetId());
    const charSel = h("select", { class: "select", id: "make-narrator", onChange: () => { make.character = charSel.value; } },
      h("option", { value: "\u0000", text: `Default (${characterName(state.plan?.default_look?.character ?? state.settings?.values?.CHARACTER)})` }),
      ...state.characters.map((c) => h("option", { value: c.slug, text: c.slug ? c.name : "Default narrator" })));
    charSel.value = make.character ?? "\u0000";
    const button = h("button", { class: "btn primary", type: "button", disabled: blocked(), onClick: () => makeTest(), "aria-busy": busy === "plan" ? "true" : "false" },
      busy === "plan" ? spinner() : icon("wand", "sm"), "Make a test video");
    return h("div", { class: "panel make-test" },
      h("div", { class: "make-test-row" },
        h("div", { class: "field" }, h("span", { class: "label", text: "Look" }),
          h("div", { class: "look-on" }, presetThumb(preset), h("span", {}, h("strong", { text: preset?.name || presetName("") }),
            h("span", { class: "help", text: preset?.good_for ? `Good for ${preset.good_for.charAt(0).toLowerCase()}${preset.good_for.slice(1)}` : "Change it in the library above." })))),
        h("div", { class: "field" }, h("label", { for: "make-narrator", text: "Narrator" }), charSel)),
      h("div", { class: "make-test-foot" },
        button,
        cancelable ? h("button", { class: "btn quiet", type: "button", onClick: () => { makeOpen = false; renderedKey = ""; render(); } }, "Cancel") : null,
        h("span", { class: "help" }, "One writer call, ", costTag(WRITER_COST), " · about a minute · the storyboard comes next, free.")));
  }

  function testCard(p, v) {
    const status = v.status === "rendered" ? h("span", { class: "chip good" }, icon("check"), "Rendered at full quality")
      : v.status === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Render failed") : h("span", { class: "chip", text: "Written, not rendered" });
    const studioOn = p.studio && p.studio.ref === p.ref && p.studio.video === v.id ? p.studio : null;
    return h("article", { class: "tv", "aria-label": v.title },
      h("div", { class: "tv-head" },
        h("div", {}, h("h3", { class: "tv-title", text: v.title }),
          h("div", { class: "pv-meta" }, h("span", { class: "chip", text: v.badge }), status,
            h("span", { class: "faint small", text: `${v.scenes ? `${v.scenes.length} scenes, ` : ""}${v.words} words${p.created ? ` · written ${ago(Date.parse(p.created) / 1000)}` : ""}` }),
            v.writer === "heuristic" ? h("span", { class: "chip warn", text: "Rule-based script" }) : null)),
        h("div", { class: "tv-link" }, presetMark(v.resolved.preset, { label: "Look" }),
          h("span", { class: "small muted tv-link-sub", text: `${v.resolved.theme ? `${v.resolved.theme.charAt(0).toUpperCase()}${v.resolved.theme.slice(1)} theme` : ""} · ${characterName(v.resolved.character)}` }))),
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
      tweakSub.textContent = "The storyboard in the current look beside the same story in another look, scene by scene, with every look control next to it.";
      compareHost.append(h("div", { class: "panel empty" },
        h("h3", { text: "Make a test video first" }),
        h("p", { text: `Pick a look above and press Make a test video. Its storyboard appears here; switching looks then shows the same story in two looks side by side.` })));
      return;
    }
    const stills = v.preview?.stills;
    const job = jobFor();
    const capturing = (job && job.kind === "preview") || busy === "look" || busy === "stills" || busy === "draft" || busy === "voice";
    const others = kept();
    const other = compareWith();
    const leftFrames = other ? other.frames || [] : [];
    const rightFrames = stills ? stills.frames || [] : [];
    const rows = Math.max(leftFrames.length, rightFrames.length, v.scenes?.length || 0, 1);
    tweakSub.textContent = stills
      ? `Storyboard ${ago(stills.at)}${stills.timing === "estimated timing" ? ", estimated timing" : ""}. Change anything below: the storyboard re-captures by itself (free, about 15 s) and the look it replaces moves to the left.`
      : "Change anything below: the storyboard re-captures by itself (free, about 15 s).";

    // the side-by-side grid: another look of this story on the left, the current look on the right
    const grid = h("div", { class: "compare", role: "table", "aria-label": "Two looks on the test video, scene by scene" });
    const leftHead = h("div", { role: "columnheader" });
    if (others.length) {
      const pick = h("select", { class: "select compare-pick", "aria-label": "Compare with", onChange: () => { compareKey = pick.value; renderedKey = ""; render(); } },
        ...others.map((e) => h("option", { value: e.key, text: `${presetName(e.look?.preset)} · ${ago(e.at)}` })));
      pick.value = other.key;
      leftHead.append(h("span", { class: "small faint", text: "Compare with" }), pick,
        h("span", { class: "small faint compare-sub", text: other.look ? lookLabel(other.look) : "" }));
    } else {
      leftHead.append(h("strong", { text: "Another look" }), h("span", { class: "small faint", text: "none captured yet" }));
    }
    grid.append(h("div", { class: "compare-head", role: "row" }, leftHead,
      h("div", { role: "columnheader" }, h("strong", { text: presetName(v.resolved.preset) }),
        h("span", { class: "small faint", text: stills ? `${plural(rightFrames.length, "scene")} · about ${formatDuration(stills.duration || 0)} · ${stills.timing === "estimated timing" ? "estimated timing" : "real timing"}`
          : capturing ? "capturing" : "no storyboard yet" }))));
    if (!others.length) {
      grid.append(h("div", { class: "compare-note" }, h("p", { class: "help" },
        "Only one look has been captured for this story. ",
        h("button", { class: "btn-link film", type: "button", disabled: blocked() || presets().length < 2, onClick: nextLook }, "Try another look"),
        " (free, about 15 s) and this storyboard moves to the left to compare.")));
    }
    for (let i = 0; i < rows; i++) {
      const left = leftFrames[i];
      const right = rightFrames[i];
      const label = right?.label || left?.label || (v.scenes?.[i] ? `${i + 1} · ${v.scenes[i]}` : `Scene ${i + 1}`);
      grid.append(h("div", { class: "compare-row", role: "row" },
        h("div", { class: "cframe", role: "cell" }, left ? h("img", { src: left.url, alt: `${presetName(other.look?.preset)}, scene ${i + 1}`, loading: "lazy" })
          : h("span", { class: "frame-empty", text: i === 0 && !others.length ? "another look appears here" : other ? "no scene here" : "" })),
        h("div", { class: "scene-label", role: "cell" }, h("span", { text: label }), (right ?? left)?.at != null ? h("span", { class: "faint tnum", text: `${(right ?? left).at.toFixed(1)} s` }) : null),
        h("div", { class: "cframe", role: "cell" }, right ? h("img", { src: right.url, alt: `${presetName(v.resolved.preset)}, scene ${i + 1}` })
          : capturing ? h("span", { class: "frame-empty skeleton" }) : h("span", { class: "frame-empty", text: i === 0 ? "no storyboard yet" : "" }))));
    }
    const captureNote = capturing ? h("div", { class: "pv-progress", role: "status" }, spinner(),
      h("span", {}, h("strong", { text: job ? (job.meta?.tier === "draft" ? "Rendering a draft clip" : "Capturing the storyboard") : "Saving the look" }),
        h("span", { class: "faint", text: job ? ` · ${job.stage || "starting"}` : "" }))) : null;
    const openFull = stills ? h("p", { class: "small" }, h("a", { class: "link", href: stills.url, target: "_blank", rel: "noopener", text: "Open the full storyboard" }),
      h("span", { class: "faint", text: ` · ready in ${Math.round(stills.seconds || 0)} s` })) : null;

    append(compareHost, [grid, captureNote, openFull, tweakPanel(p, v), actionsRow(p, v)]);
    if (v.preview?.draft) compareHost.append(draftBlock(v.preview.draft));
    if (others.length) {
      compareHost.append(h("div", { class: "earlier-looks" },
        h("h4", { text: "Earlier looks on this video" }),
        h("div", { class: "earlier-strip" }, others.slice(0, 6).map((e) => {
          const on = e.key === other?.key;
          return h("button", { class: `earlier-look${on ? " on" : ""}`, type: "button", "aria-pressed": String(on), onClick: () => { compareKey = e.key; renderedKey = ""; render(); } },
            h("img", { src: e.url, alt: `Storyboard in ${presetName(e.look?.preset)}`, loading: "lazy" }),
            h("span", { class: "small" }, h("strong", { text: presetName(e.look?.preset) }), h("span", { class: "faint", text: ago(e.at) })));
        }))));
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
    const presetPick = presetSelect({ value: effectivePresetId(v.resolved.preset), id: "tweak-preset", disabled: off,
      onChange: (val) => { setBench({ preset: val }); applyLook({ preset: val }); } });
    const charSel = h("select", { class: "select", id: "tweak-narrator", disabled: off, onChange: () => applyLook({ character: charSel.value === "\u0000" ? "" : charSel.value }) },
      h("option", { value: "\u0000", text: `Default (${characterName(d.character)})` }),
      ...state.characters.map((c) => h("option", { value: c.slug, text: c.slug ? c.name : "Default narrator" })));
    charSel.value = v.look.character !== undefined ? v.look.character : "\u0000";
    const explainer = v.resolved.style === "explainer";
    const nextBtn = h("button", { class: "btn small", type: "button", disabled: off || presets().length < 2, onClick: nextLook,
      title: "Switch to the next look in the library and re-capture the storyboard" },
    busy === "look" ? spinner() : icon("shuffle", "sm"), "Try another look", costTag("free · 15 s"));
    const field = (label, control, hint) => h("div", { class: `tweak-field${hint ? " hinted" : ""}` }, h("span", { class: "label", text: label }), control, hint ? h("span", { class: "help", text: hint }) : null);
    return h("div", { class: "tweak", "aria-label": "Look controls" },
      field("Look preset", h("div", { class: "tweak-recipe" }, presetPick, nextBtn), explainer ? null : "Presets apply to the Explainer style."),
      field("Style", seg("Style", [["explainer", "Explainer"], ["avatar", "Avatar"]], "style")),
      field("Narrator", charSel),
      explainer ? field("Theme", seg("Theme", [["light", "Light"], ["dark", "Dark"], ["cinematic", "Cinematic"]], "theme"), v.look.theme ? "Set by hand; picking a preset resets it." : "From the preset.")
        : field("Background", seg("Background", [["gameplay", "Gameplay"], ["none", "Plain"]], "background")),
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
    const v = video();
    const stills = v?.preview?.stills;
    const others = v ? kept() : [];
    const chosen = presetName(currentPresetId());
    const current = v ? 3 : state.bench.preset ? 2 : 1;
    const items = [
      ["Look library", v ? `${chosen} on the test video` : state.bench.preset ? `${chosen} picked` : `${plural(presets().length, "look")} to choose from`],
      ["Test video", v ? `${v.title.slice(0, 40)}${v.title.length > 40 ? "…" : ""}` : "none yet"],
      ["Tweak and compare", stills ? `storyboard ${ago(stills.at)}${others.length ? ` · ${plural(others.length, "other look")}` : ""}` : v ? "no storyboard yet" : "waiting for a test video"],
    ];
    rail.replaceChildren(...items.map(([label, sub], i) => h("li", { dataset: { s: i + 1 < current ? "done" : i + 1 === current ? "current" : "todo" }, "aria-current": i + 1 === current ? "step" : null },
      h("span", { class: "rail-n", "aria-hidden": "true" }, i + 1 < current ? icon("check") : String(i + 1)),
      h("span", { class: "rail-text" }, h("strong", { text: label }), h("span", { class: "rail-sub", text: sub })))));
    const job = runningJob();
    let hint;
    if (job) hint = ["Running: ", h("strong", { text: job.stage || "starting" })];
    else if (!v) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: () => makeTest() }, "make a test video"), " in ", h("strong", { text: chosen }), " (one writer call, ", costTag(WRITER_COST), ")"];
    else if (!stills) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: () => preview("stills") }, "capture the storyboard"), " (free, about 15 s)"];
    else if (!others.length) hint = ["Next: ", h("button", { class: "btn-link", type: "button", onClick: nextLook }, "try another look"), " to see this story in two looks side by side (free, about 15 s)"];
    else hint = ["Next: compare, settle on the look you like, then ", h("strong", { text: "Render final" }), " when you are happy."];
    next.replaceChildren(...hint);
    const l = state.ledger;
    const cents = l.writer * 3;
    ledger.replaceChildren(h("strong", { text: "This session" }),
      h("span", { text: `${plural(l.writer, "writer call")} (about ${cents < 100 ? `${cents}¢` : `$${(cents / 100).toFixed(2)}`}) · ${plural(l.previews, "free preview")} · ${plural(l.renders, "render")}` }));
  }

  // ---- render all ----
  function render() {
    const job = runningJob();
    const key = JSON.stringify([state.looks, state.plans?.plans, state.plan, state.bench, busy, compareKey, job?.id, job?.state, job?.stage, state.characters.length, isRunning(), makeOpen, state.ledger, state.settings?.values?.LOOK_PRESET]);
    if (key === renderedKey) return;
    renderedKey = key;
    renderRail();
    renderLibrary();
    renderTest();
    renderCompare();
  }

  // ---- loading ----
  async function refreshPlans(preferRef) {
    try {
      const data = await loadPlans();
      const refs = data.plans.filter((p) => isTestRef(p.ref)).map((p) => p.ref);
      const want = preferRef && refs.includes(preferRef) ? preferRef
        : state.bench.planRef && refs.includes(state.bench.planRef) ? state.bench.planRef : refs[0] || null;
      if (want !== state.bench.planRef) setBench({ planRef: want });
      if (!want) { if (state.plan && isTestRef(state.plan.ref)) await loadPlan(null); }
      else await loadPlan(want);
    } catch { /* shown on the next poll */ }
    render();
  }

  render();
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

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}
