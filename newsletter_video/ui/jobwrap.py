"""Tiny supervisor that keeps a background job's bookkeeping on disk, so the
control panel can be closed or restarted mid-run and still pick the job up.

    python -m newsletter_video.ui.jobwrap JOB_DIR -- <argv...>

It starts <argv> (no shell), sends its output to JOB_DIR/log.txt, and writes
JOB_DIR/result.json when the process ends. SIGTERM stops the child and records
the job as cancelled.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

KILL_GRACE_SECONDS = 10


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if "--" not in argv or len(argv) < 3:
        print("usage: jobwrap JOB_DIR -- argv...", file=sys.stderr)
        return 2
    job_dir = Path(argv[0])
    command = argv[argv.index("--") + 1:]
    cancelled = {"flag": False}
    child: dict[str, subprocess.Popen | None] = {"p": None}

    def on_term(signum, frame):  # noqa: ARG001
        cancelled["flag"] = True
        process = child["p"]
        if process and process.poll() is None:
            process.terminate()
            timer = threading.Timer(KILL_GRACE_SECONDS, process.kill)  # in case it ignores SIGTERM
            timer.daemon = True
            timer.start()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    started = time.time()
    with (job_dir / "log.txt").open("ab", buffering=0) as log:
        try:
            child["p"] = process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log.write(f"Could not start the job: {exc}\n".encode())
            _write_json(job_dir / "result.json", {"exit_code": 127, "cancelled": False,
                                                  "finished_at": time.time()})
            return 127
        _write_json(job_dir / "child.json", {"pid": process.pid, "started_at": started})
        code = process.wait()
    _write_json(job_dir / "result.json", {"exit_code": code, "cancelled": cancelled["flag"],
                                          "finished_at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
