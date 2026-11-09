// The look library's vocabulary, shared by the workbench, the plan panel and
// Settings: naming a preset, its thumbnail, a grid of preset cards to pick
// from, and a compact picker (thumbnail beside a select) for tight spaces.
import { h, icon } from "./dom.js";
import { presets, presetInfo, effectivePresetId } from "./store.js";

/** The preset's name; "" (no preset) names the library's default look. */
export function presetName(id) {
  const p = presetInfo(effectivePresetId(id));
  return p ? p.name : id || "Default look";
}

export function presetThumb(p, cls = "") {
  return h("span", { class: `look-thumb${cls ? ` ${cls}` : ""}`, "aria-hidden": "true" },
    p?.thumbnail ? h("img", { src: p.thumbnail, alt: "", loading: "lazy" }) : icon("palette"));
}

/** A thumbnail and name in one line: "the look on the test video". */
export function presetMark(id, { label } = {}) {
  const p = presetInfo(effectivePresetId(id));
  return h("span", { class: "look-mark" }, presetThumb(p, "sm"),
    h("span", {}, label ? h("span", { class: "faint small", text: label }) : null, h("strong", { text: presetName(id) })));
}

/** The library as a grid of cards. `value` is the selected preset id;
 * `badges(p)` may return chips for a card; `disabled` greys the whole grid. */
export function presetGrid({ value, onSelect, badges = () => [], disabled = false, name = "look-preset" }) {
  const current = effectivePresetId(value);
  return h("div", { class: "look-grid", role: "radiogroup", "aria-label": "Look library" },
    presets().map((p) => {
      const on = p.id === current;
      const id = `${name}-${p.id}`;
      const input = h("input", { type: "radio", name, id, value: p.id, checked: on, disabled, onChange: () => onSelect(p.id) });
      return h("label", { class: `look-card${on ? " on" : ""}`, for: id }, input,
        presetThumb(p),
        h("span", { class: "look-card-body" },
          h("span", { class: "look-name" }, h("span", { text: p.name }), ...badges(p)),
          h("span", { class: "look-desc", text: p.description.replace(/\s*\([^)]*\)\s*$/, "") }),
          p.good_for ? h("span", { class: "look-for" }, h("strong", { text: "Good for " }), p.good_for) : null),
        h("span", { class: "tick", "aria-hidden": "true" }, icon("check")));
    }));
}

/** A compact picker: the chosen preset's thumbnail beside a select. With
 * `defaultLabel`, a first option (value "") stands for "inherit the default".
 * Returns the element; `el.sync(value)` repaints it, `el.value` reads it. */
export function presetSelect({ value, onChange, defaultLabel = null, id = null, disabled = false, ariaLabel = "Look preset" }) {
  const thumbHost = h("span", { class: "look-pick-thumb" });
  const select = h("select", { class: "select", id, disabled, "aria-label": id ? null : ariaLabel, onChange: () => { paint(select.value); onChange(select.value); } },
    defaultLabel ? h("option", { value: "", text: defaultLabel }) : null,
    ...presets().map((p) => h("option", { value: p.id, text: p.default && !defaultLabel ? `${p.name} (default)` : p.name })));
  const el = h("div", { class: "look-pick" }, thumbHost, select);
  function paint(v) {
    const p = presetInfo(effectivePresetId(v));
    thumbHost.replaceChildren(presetThumb(p, "sm"));
  }
  el.sync = (v) => { select.value = v ?? ""; paint(select.value); };
  Object.defineProperty(el, "value", { get: () => select.value });
  el.select = select;
  el.sync(value);
  return el;
}
