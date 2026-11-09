// The colour guard, as the panel's live preview needs it. Same arithmetic as
// newsletter_video/colors.py (which is what a render actually uses): the glass
// look needs a dark ground, paper a light one, and the accent must stand out
// against whichever ground is chosen. Hex only.
export const HEX = /^#[0-9a-f]{6}$/i;

export function normaliseHex(value) {
  let v = String(value || "").trim().toLowerCase().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/.test(v)) v = [...v].map((c) => c + c).join("");
  return /^[0-9a-f]{6}$/.test(v) ? `#${v}` : null;
}

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const toHex = (c) => `#${c.map((x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0")).join("")}`;

export function luminance(hex) {
  const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = rgb(hex);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function push(hex, toward, ok) {
  const c = rgb(hex);
  let out = hex;
  for (let i = 1; i <= 40 && !ok(out); i++) out = toHex(c.map((x, k) => x + (toward[k] - x) * (i / 40)));
  return out;
}

export const PAPER_BG_MIN_LUM = 0.55;
export const GLASS_BG_MAX_LUM = 0.06;
export const ACCENT_MIN_CONTRAST = 3.0;

/** {bg, accent, notes}: unset colours stay null; `notes` says in words what was adjusted. */
export function fitColors(dark, bg, accent, baseBg) {
  const notes = [];
  let outBg = normaliseHex(bg);
  let outAccent = normaliseHex(accent);
  if (outBg) {
    if (dark && luminance(outBg) > GLASS_BG_MAX_LUM) {
      const fixed = push(outBg, [0, 0, 0], (c) => luminance(c) <= GLASS_BG_MAX_LUM);
      notes.push(`Background darkened to ${fixed}: the dark glass look needs a dark ground to keep its light text readable.`);
      outBg = fixed;
    } else if (!dark && luminance(outBg) < PAPER_BG_MIN_LUM) {
      const fixed = push(outBg, [255, 255, 255], (c) => luminance(c) >= PAPER_BG_MIN_LUM);
      notes.push(`Background lightened to ${fixed}: the clean paper look needs a light ground to keep its dark text readable.`);
      outBg = fixed;
    }
  }
  if (outAccent) {
    const ground = outBg || baseBg || (dark ? "#050509" : "#f6f4ee");
    if (contrast(outAccent, ground) < ACCENT_MIN_CONTRAST) {
      const fixed = push(outAccent, dark ? [255, 255, 255] : [0, 0, 0], (c) => contrast(c, ground) >= ACCENT_MIN_CONTRAST);
      notes.push(`Animation colour ${dark ? "lightened" : "darkened"} to ${fixed} so it stands out against the background.`);
      outAccent = fixed;
    }
  }
  return { bg: outBg, accent: outAccent, notes };
}

/** Readable ink for a ground, for the preview swatch. */
export const inkFor = (bg) => (luminance(bg) > 0.4 ? "#17191e" : "#f4f3ff");
