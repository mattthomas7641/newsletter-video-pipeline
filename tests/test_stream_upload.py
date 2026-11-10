"""Uploading each video while the next one renders (stream_upload.py)."""
from __future__ import annotations

import threading
from datetime import date

from newsletter_video.runstate import load_state, record_video, update_run
from newsletter_video.stream_upload import StreamingUploader


def test_each_submit_triggers_an_upload_and_close_waits_for_them():
    calls = []
    up = StreamingUploader(date(2026, 11, 10), upload=lambda day: calls.append(day))
    for _ in range(3):
        up.submit()
    up.close(timeout=5)
    assert calls == [date(2026, 11, 10)] * 3


def test_a_failing_upload_never_reaches_the_render_loop_and_later_ones_still_run():
    calls = []

    def upload(day):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("network")

    up = StreamingUploader(date(2026, 11, 10), upload=upload)
    up.submit()
    up.submit()
    up.close(timeout=5)
    assert len(calls) == 2


def test_status_updates_from_two_threads_do_not_lose_each_other(tmp_path):
    names = [f"v{i:02d}" for i in range(20)]

    def writer(prefix):
        for n in names:
            record_video(tmp_path, f"{prefix}{n}", "rendered")
            update_run(tmp_path, **{f"{prefix}{n}": True})

    threads = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    state = load_state(tmp_path)
    assert len(state["videos"]) == 40
    assert len(state["run"]) == 40
