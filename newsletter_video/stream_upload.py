"""Uploads each video the moment it is rendered, while the next one renders.

The daily run used to render every video and only then upload them all, so the first video waited for the last.
With a StreamingUploader the render loop hands each finished video over (after its sidecar is written) and a single
background worker uploads whatever is pending for the day. It never raises into the render loop: a failure is logged
and the video stays pending, so the run's final upload step (which also reports sign-in problems) picks it up.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import date

log = logging.getLogger("newsletter_video")

_DONE = object()


class StreamingUploader:
    def __init__(self, day: date, upload=None):
        if upload is None:
            from .youtube_upload import upload_day as upload
        self.day = day
        self._upload = upload
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="stream-upload", daemon=True)
        self._thread.start()

    def submit(self) -> None:
        """A video just finished: upload what is pending."""
        self._queue.put(1)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is _DONE:
                return
            try:
                self._upload(self.day)
            except Exception as exc:  # noqa: BLE001 - the final upload step retries and reports
                log.warning("Streaming upload did not finish (%s: %s); the video stays pending", type(exc).__name__, exc)

    def close(self, timeout: float | None = None) -> None:
        """Waits for the queue to drain, then stops the worker."""
        self._queue.put(_DONE)
        self._thread.join(timeout)
