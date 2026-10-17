import { h, icon, spinner, nextId, formatClock } from "../dom.js";
import { savePill, notYetNote } from "../components.js";
import { state, subscribe, value, setDraft, loadSetup } from "../store.js";

const PRIVACY = [
  ["private", "Private", "Only you can see them until you publish."],
  ["unlisted", "Unlisted", "Anyone with the link can watch. Not shown in search."],
  ["public", "Public", "Everyone can see them straight away."],
];

export function mountSettings(root, ctx) {
  const cleanup = [];
  const onCleanup = (fn) => cleanup.push(fn);

  const head = h("header", { class: "page-head" },
    h("div", {}, h("h1", { text: "Settings" }),
      h("p", { class: "lede", text: "Everyday options for the daily run. Nothing changes until you save, and saved changes apply from the next run." })),
    savePill(onCleanup));

  // ---- videos per day ----
  const perDayId = nextId("perday");
  const perDay = h("input", { id: perDayId, type: "number", inputmode: "numeric", min: 1, max: 20, step: 1, "aria-describedby": `${perDayId}-help`,
    onInput: () => commitPerDay(false), onBlur: () => commitPerDay(true) });
  const minus = h("button", { type: "button", "aria-label": "One fewer video", onClick: () => bump(-1) }, "−");
  const plus = h("button", { type: "button", "aria-label": "One more video", onClick: () => bump(1) }, icon("plus", "sm"));
  const perDayError = h("p", { class: "error-text", role: "alert", hidden: true });
  const perDayHelp = h("p", { class: "help", id: `${perDayId}-help` });
  function commitPerDay(final) {
    const n = Number(perDay.value);
    const ok = Number.isInteger(n) && n >= 1 && n <= 20;
    perDayError.hidden = ok;
    perDay.setAttribute("aria-invalid", ok ? "false" : "true");
    if (ok) setDraft("VIDEOS_PER_DAY", n);
    else {
      perDayError.textContent = "Enter a whole number from 1 to 20.";
      if (final) { perDay.value = value("VIDEOS_PER_DAY"); perDayError.hidden = true; perDay.setAttribute("aria-invalid", "false"); }
    }
  }
  function bump(delta) {
    const next = Math.min(20, Math.max(1, Number(value("VIDEOS_PER_DAY")) + delta));
    setDraft("VIDEOS_PER_DAY", next);
  }

  // ---- upload ----
  const uploadId = nextId("upload");
  const uploadBox = h("input", { id: uploadId, type: "checkbox", role: "switch", "aria-describedby": `${uploadId}-help`,
    onChange: () => setDraft("YOUTUBE_UPLOAD", uploadBox.checked ? "1" : "0") });
  const uploadState = h("span", { class: "state" });
  const uploadSwitch = h("label", { class: "switch" }, uploadBox, h("span", { class: "track" }), uploadState);
  const privacyName = nextId("privacy");
  const privacySeg = h("div", { class: "seg", role: "radiogroup", "aria-labelledby": `${privacyName}-l` },
    PRIVACY.map(([v, label]) => h("label", {},
      h("input", { type: "radio", name: privacyName, value: v, onChange: () => setDraft("YOUTUBE_PRIVACY", v) }),
      h("span", { class: "opt", text: label }))));
  const privacyHelp = h("p", { class: "help" });
  const publicNote = h("div");

  // ---- writer failure ----
  const fbName = nextId("fallback");
  const fbSeg = h("div", { class: "seg", role: "radiogroup", "aria-labelledby": `${fbName}-l` },
    [["0", "Hold for review"], ["1", "Upload anyway"]].map(([v, label]) => h("label", {},
      h("input", { type: "radio", name: fbName, value: v, onChange: () => setDraft("FALLBACK_UPLOAD", v) }),
      h("span", { class: "opt", text: label }))));
  const fbHelp = h("p", { class: "help" });
  const fbNote = h("div");
  const writerNote = h("div");

  const row = (labelText, labelId, help, control, extra) => h("div", { class: "row" },
    h("div", {}, h("span", { class: "label", id: labelId, text: labelText }), help, extra),
    h("div", { class: "control" }, control));

  const production = h("section", { class: "section", "aria-labelledby": "prod-h" },
    h("div", { class: "section-head" }, h("h2", { id: "prod-h", text: "Daily run" })),
    h("div", { class: "panel rows" },
      h("div", { class: "row" },
        h("div", {}, h("label", { class: "label", for: perDayId, text: "Videos per day" }), perDayHelp),
        h("div", { class: "control" }, h("div", { class: "stepper" }, minus, perDay, plus), perDayError)),
      row("Runs at", "sched-l", h("p", { class: "help", id: "sched-help" }), h("span", { class: "chip", id: "sched-chip" }))));

  const youtube = h("section", { class: "section", "aria-labelledby": "yt-h" },
    h("div", { class: "section-head" }, h("h2", { id: "yt-h", text: "YouTube" })),
    h("div", { class: "panel rows" },
      h("div", { class: "row" },
        h("div", {}, h("label", { class: "label", for: uploadId, text: "Upload to YouTube" }),
          h("p", { class: "help", id: `${uploadId}-help`, text: "Post each finished video automatically after the run. Turn this off to only keep the files." })),
        h("div", { class: "control" }, uploadSwitch)),
      h("div", { class: "row", id: "privacy-row" },
        h("div", {}, h("span", { class: "label", id: `${privacyName}-l`, text: "Who can watch" }), privacyHelp, publicNote),
        h("div", { class: "control" }, privacySeg))));

  const writer = h("section", { class: "section", "aria-labelledby": "wr-h" },
    h("div", { class: "section-head" }, h("h2", { id: "wr-h", text: "If the AI writer fails" }),
      h("p", { text: "Sometimes the writing step can't finish (an outage, a missing key). The pipeline then falls back to simpler, rule-based scripts." })),
    writerNote,
    h("div", { class: "panel rows" },
      h("div", { class: "row" },
        h("div", {}, h("span", { class: "label", id: `${fbName}-l`, text: "What should happen to those videos?" }), fbHelp, fbNote),
        h("div", { class: "control" }, fbSeg))));

  // ---- setup check ----
  const checksHost = h("div");
  const recheck = h("button", { class: "btn small", type: "button", onClick: runCheck }, icon("refresh", "sm"), "Check again");
  const setup = h("section", { class: "section", "aria-labelledby": "setup-h", id: "setup" },
    h("div", { class: "section-head" }, h("h2", { id: "setup-h", text: "Setup check" }),
      h("p", { text: "What this computer has ready. Only whether something is present is shown, never the secret itself." })),
    checksHost, h("div", { class: "form-actions" }, recheck));

  root.append(head, production, youtube, writer, setup);

  async function runCheck() {
    recheck.disabled = true;
    recheck.replaceChildren(spinner(), "Checking");
    try { await loadSetup(); } catch { /* shown by renderChecks */ }
    recheck.disabled = false;
    recheck.replaceChildren(icon("refresh", "sm"), "Check again");
  }

  function renderChecks() {
    const data = state.setup;
    if (!data) {
      checksHost.replaceChildren(h("div", { class: "panel panel-pad" }, h("div", { class: "skeleton sk-block", "aria-hidden": "true" }), h("p", { class: "help", text: "Checking your setup" })));
      return;
    }
    checksHost.replaceChildren(h("ul", { class: "panel checks" }, data.checks.map((c) => {
      const tone = c.ok ? "good" : c.optional ? "neutral" : "warn";
      return h("li", {},
        h("span", { class: `mark ${tone}` }, icon(c.ok ? "check" : c.optional ? "info" : "alert")),
        h("div", {}, h("strong", { text: c.label }), h("span", { class: "d", text: c.detail })));
    })));
  }

  function sync() {
    const perDayValue = value("VIDEOS_PER_DAY");
    if (document.activeElement !== perDay) perDay.value = perDayValue;
    minus.disabled = Number(perDayValue) <= 1;
    plus.disabled = Number(perDayValue) >= 20;
    const n = Number(perDayValue);
    perDayHelp.textContent = `Each video takes roughly 8 minutes to make, so ${n === 1 ? "one video" : `${n} videos`} ${n === 1 ? "takes" : "take"} about ${approxTime(n)}.`;

    const uploading = value("YOUTUBE_UPLOAD") === "1";
    uploadBox.checked = uploading;
    uploadState.textContent = uploading ? "On" : "Off";
    for (const input of privacySeg.querySelectorAll("input")) {
      input.checked = input.value === value("YOUTUBE_PRIVACY");
      input.disabled = !uploading;
    }
    root.querySelector("#privacy-row").toggleAttribute("data-off", !uploading);
    const privacy = PRIVACY.find(([v]) => v === value("YOUTUBE_PRIVACY"));
    privacyHelp.textContent = uploading ? privacy[2] : "Only used when uploading is on.";
    publicNote.replaceChildren();
    if (uploading && value("YOUTUBE_PRIVACY") === "public") {
      publicNote.append(h("p", { class: "help", text: "Choose Private if you want to review videos in YouTube Studio before anyone else sees them." }));
    }

    for (const input of fbSeg.querySelectorAll("input")) input.checked = input.value === value("FALLBACK_UPLOAD");
    fbHelp.textContent = value("FALLBACK_UPLOAD") === "1"
      ? "They are uploaded like any other video, which keeps your schedule going but they read more plainly."
      : "They are rendered and saved, but not uploaded. You decide what to do with them.";
    const supported = state.settings?.supported || {};
    fbNote.replaceChildren(notYetNote(supported.FALLBACK_UPLOAD) || "");
    writerNote.replaceChildren();
    if (state.settings && !state.settings.keys.ANTHROPIC_API_KEY) {
      writerNote.append(h("div", { class: "notice warn" }, icon("alert"), h("div", { class: "notice-body" },
        h("p", { text: "No Anthropic API key is set, so every run uses the simpler writer." }),
        h("p", { text: "Add ANTHROPIC_API_KEY to your .env file to use the AI writer. Keys are never shown or edited in this panel." }))));
    }

    const sched = state.runs.schedule;
    const chip = root.querySelector("#sched-chip");
    const help = root.querySelector("#sched-help");
    if (sched?.time) {
      chip.textContent = `${formatClock(sched.time)} every day`;
      chip.className = `chip ${sched.loaded === false ? "warn" : "good"}`;
      help.textContent = sched.loaded === false
        ? "The schedule exists but isn't loaded in macOS, so it won't run on its own."
        : "Set by the macOS scheduled job, which reads your saved settings each time. The time can't be changed from here.";
    } else {
      chip.textContent = "Not scheduled";
      chip.className = "chip";
      help.textContent = "No scheduled job was found. You can still run the batch by hand from the Studio page.";
    }
    renderChecks();
  }

  sync();
  onCleanup(subscribe(sync));
  if (!state.setup) loadSetup().catch(() => {});

  return () => { cleanup.forEach((fn) => fn()); root.replaceChildren(); };
}

function approxTime(n) {
  const minutes = n * 8;
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} hour${hours > 1 ? "s" : ""} ${rest} minutes` : `${hours} hour${hours > 1 ? "s" : ""}`;
}
