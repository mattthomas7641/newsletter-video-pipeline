"""The style library: reference videos the owner likes, analysed into our
vocabulary, recreated with our composition, kept as searchable recipes, and
drawn on by a planner when new videos are written.

    python -m newsletter_video.styles add inbox/clip.mp4 --name punchy-type --notes "..."
    python -m newsletter_video.styles search "kinetic type with wipe transitions"
    python -m newsletter_video.styles plan --story output/2026-10-13/07_x.json

In-process entry points for the pipeline: `library_enabled()` (the library
is on and non-empty) and `style_scene_plan(plan, title)` (adds validated
`style` fields to a ScenePlan's scenes, or leaves it untouched).

Everything here is inert unless STYLE_LIBRARY is on (the default folder
exists and has entries): with an empty or disabled library the pipeline
renders exactly as before.
"""
from __future__ import annotations

import logging

from .library import library_enabled, open_library

log = logging.getLogger("newsletter_video")


def style_scene_plan(plan, title: str = "", call=None) -> int:
    """Plan and attach styles for one validated ScenePlan. Returns the number
    of scenes styled (0 when the library is off/empty or anything fails:
    the plan is then exactly as it was)."""
    try:
        if not library_enabled():
            return 0
        from .planner import apply_style_plan, plan_styles

        style_plan = plan_styles(plan.to_list(), open_library(), call=call, title=title)
        if style_plan is None:
            return 0
        n = apply_style_plan(plan, style_plan)
        if n:
            log.info("Style library: %d of %d scenes styled for %r (%s)", n, len(plan.scenes), title,
                     ", ".join(style_plan.recipes_used) or "overrides only")
        return n
    except Exception as exc:  # noqa: BLE001 - styling is optional; the video must still be made
        log.warning("Style library planning failed for %r (%s: %s); default look kept", title, type(exc).__name__, exc)
        return 0


__all__ = ["library_enabled", "open_library", "style_scene_plan"]
