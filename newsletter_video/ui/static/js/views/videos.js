import { h, icon, formatBytes, formatDay, plural, spinner } from "../dom.js";
import { state, loadVideos } from "../store.js";

export function mountVideos(root) {
  const host = h("div");
  const refresh = h("button", { class: "btn small", type: "button", onClick: load }, icon("refresh", "sm"), "Refresh");
  root.append(
    h("header", { class: "page-head" },
      h("div", {}, h("h1", { text: "Videos" }),
        h("p", { class: "lede", text: "Test videos and the last two weeks of daily videos. Older files are cleared automatically once they are uploaded." })),
      refresh),
    host);
  let key = "";

  function tile(v, { test = false } = {}) {
    const media = v.url
      ? h("video", { src: `${v.url}#t=0.1`, controls: true, preload: "metadata", playsinline: true, "aria-label": v.title })
      : h("div", { class: "nofile" }, "Video file removed to save space");
    const status = test ? { state: "Test", tone: "neutral" } : v.status;
    return h("div", { class: "vtile" }, media,
      h("div", { class: "t", title: v.title, text: v.title }),
      h("div", { class: "meta" },
        h("span", { class: `chip ${status.tone === "neutral" ? "" : status.tone}`, text: status.state || "Ready" }),
        v.size ? h("span", { class: "faint", text: formatBytes(v.size) }) : null,
        v.youtube ? h("a", { class: "link inline", href: v.youtube.url, target: "_blank", rel: "noopener noreferrer" }, "YouTube", icon("link", "sm")) : null),
      status.error ? h("div", { class: "error-text", text: status.error }) : null);
  }

  function render() {
    const data = state.videos;
    const next = JSON.stringify(data);
    if (next === key) return;
    key = next;
    host.replaceChildren();
    if (!data) {
      host.append(h("div", { class: "vgrid" }, [1, 2, 3, 4].map(() => h("div", { class: "vtile" }, h("div", { class: "skeleton vsk" })))));
      return;
    }
    const days = [data.today, ...data.days].filter((d) => d.videos.length);
    if (!days.length && !data.previews.length) {
      host.append(h("div", { class: "panel empty" },
        h("h3", { text: "No videos yet" }),
        h("p", { text: "Videos from the daily run and your test videos will show up here." }),
        h("a", { class: "btn primary", href: "#/" }, "Go to Studio")));
      return;
    }
    if (data.previews.length) {
      host.append(h("section", { class: "day", "aria-labelledby": "tests-h" },
        h("h2", { id: "tests-h" }, "Test videos", h("span", { text: "Never uploaded. Kept in output/_previews." })),
        h("div", { class: "vgrid" }, data.previews.map((p) => tile({ ...p, size: 0 }, { test: true })))));
    }
    days.forEach((day, i) => {
      const id = `day-${day.date}`;
      host.append(h("section", { class: "day", "aria-labelledby": id },
        h("h2", { id }, i === 0 && day.date === data.today.date ? "Today" : formatDay(day.date), h("span", { text: plural(day.videos.length, "video") })),
        h("div", { class: "vgrid" }, day.videos.map((v) => tile(v)))));
    });
  }

  async function load() {
    refresh.disabled = true;
    refresh.replaceChildren(spinner(), "Refreshing");
    try { await loadVideos(); } catch (err) {
      host.replaceChildren(h("div", { class: "notice bad", role: "alert" }, icon("alert"), h("div", { class: "notice-body" }, h("p", { text: err.message }))));
      key = "";
    }
    refresh.disabled = false;
    refresh.replaceChildren(icon("refresh", "sm"), "Refresh");
    render();
  }

  state.videos = null;
  render();
  load();
  return () => root.replaceChildren();
}
