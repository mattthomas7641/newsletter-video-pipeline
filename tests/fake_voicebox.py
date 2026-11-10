"""A local stand-in for the Voicebox app's HTTP API, with switchable
failure modes:

  normal        generate -> status stream completes -> valid audio
  wedged        status stream sends heartbeats forever, never completes
  silent_stream status stream sends nothing at all (connection held open)
  bad_audio     completes, but the audio is a silent clip
  empty_audio   completes, but the audio download is empty

`mode` can be changed while the server runs (a test flips it to simulate
a restart fixing things).
"""
from __future__ import annotations

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile


def wav_bytes(seconds: float = 3.0, silent: bool = False) -> bytes:
    rate = 16000
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    samples = np.zeros_like(t) if silent else 0.4 * np.sin(2 * np.pi * 200 * t)
    buf = io.BytesIO()
    soundfile.write(buf, samples.astype("float32"), rate, format="WAV")
    return buf.getvalue()


class FakeVoicebox:
    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.generations = 0
        self.stopping = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep pytest output clean
                pass

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    return self._json(200, {"status": "ok"})
                if self.path.startswith("/generate/") and self.path.endswith("/status"):
                    return self._stream()
                if self.path.startswith("/audio/"):
                    if server.mode == "empty_audio":
                        data = b""
                    else:
                        data = wav_bytes(silent=server.mode == "bad_audio")
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if self.path == "/generate":
                    length = int(self.headers.get("Content-Length", 0))
                    json.loads(self.rfile.read(length) or b"{}")
                    server.generations += 1
                    return self._json(200, {"id": f"gen{server.generations}", "status": "generating"})
                self._json(404, {"error": "not found"})

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    if server.mode in ("normal", "bad_audio", "empty_audio"):
                        self.wfile.write(b'data: {"status": "generating"}\n\n')
                        self.wfile.flush()
                        time.sleep(0.05)
                        self.wfile.write(b'data: {"status": "completed"}\n\n')
                        self.wfile.flush()
                        return
                    while not server.stopping.is_set():
                        if server.mode == "wedged":  # heartbeats that keep the socket alive forever
                            self.wfile.write(b': heartbeat\n\n' if server.generations % 2 else
                                             b'data: {"status": "generating"}\n\n')
                            self.wfile.flush()
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "FakeVoicebox":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()
