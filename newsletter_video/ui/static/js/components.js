import { h, icon, spinner } from "./dom.js";
import { state, subscribe, isDirty, dirtyKeys, save, discard } from "./store.js";

// ---- toasts ----

export function toast(message, tone = "good", ms = 6000) {
  const host = document.getElementById("toasts");
  const node = h("div", { class: `toast ${tone}` },
    icon(tone === "bad" ? "alert" : "check"),
    h("span", { text: message }),
    h("button", { type: "button", "aria-label": "Dismiss", onClick: () => node.remove() }, icon("x", "sm")));
  host.append(node);
  if (ms) setTimeout(() => node.remove(), ms);
}

// ---- saved / unsaved pill shown in page headers ----

export function savePill(registerCleanup) {
  const el = h("span", { class: "chip" });
  const update = () => {
    el.replaceChildren();
    if (state.saving) {
      el.className = "chip active";
      el.append(spinner(), "Saving");
    } else if (isDirty()) {
      el.className = "chip warn";
      el.append(icon("alert"), "Unsaved changes");
    } else {
      el.className = "chip good";
      el.append(icon("check"), state.justSaved ? "Saved" : "All changes saved");
    }
  };
  update();
  registerCleanup(subscribe(update));
  return el;
}

// ---- sticky save bar (always mounted; visible only when there is something to save) ----

export function mountSaveBar(slot) {
  const render = () => {
    slot.replaceChildren();
    if (!isDirty() && !state.saving) return;
    const count = dirtyKeys().length;
    const bar = h("div", { class: `savebar${state.saveError ? " error" : ""}`, role: "region", "aria-label": "Unsaved changes" },
      h("span", { class: "msg" }, h("span", { class: "dot" }),
        count === 1 ? "1 unsaved change" : `${count} unsaved changes`),
      h("span", { class: "faint small" }, "Applies from the next run"),
      h("span", { class: "grow" }),
      h("button", { class: "btn quiet", type: "button", disabled: state.saving, onClick: discard }, "Discard"),
      h("button", { class: "btn primary", type: "button", disabled: state.saving, "aria-busy": state.saving ? "true" : "false",
        onClick: async () => {
          const ok = await save();
          if (ok) toast("Saved. These choices apply from the next run, including the scheduled one.");
        } }, state.saving ? [spinner(), "Saving"] : "Save changes"),
      state.saveError ? h("p", { class: "err", role: "alert", text: state.saveError }) : null);
    slot.append(bar);
  };
  let last = "";
  subscribe(() => {
    const key = JSON.stringify([state.draft, state.saving, state.saveError]);
    if (key !== last) { last = key; render(); }
  });
  render();
}

// ---- confirmation: the one modal in the panel, for steps that cost money or make real videos ----

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

// ---- a cost or time tag next to an action: "free · 15 s", "about 3 cents" ----

export function costTag(text, tone = "") {
  return h("span", { class: `cost${tone ? ` ${tone}` : ""}`, text });
}

// ---- "2 min ago" from an epoch in seconds ----

export function ago(epochSeconds) {
  if (!epochSeconds) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const hrs = Math.round(m / 60);
  if (hrs < 24) return `${hrs} h ago`;
  return `${Math.round(hrs / 24)} d ago`;
}

// ---- a note that a setting is not read by this version of the pipeline yet ----

export function notYetNote(supported) {
  return supported ? null : h("div", { class: "notice inline" }, icon("info"),
    h("div", { class: "notice-body" }, h("p", { class: "small",
      text: "Not active in this version of the pipeline yet. Your choice is saved and takes effect once the matching update is installed." })));
}
