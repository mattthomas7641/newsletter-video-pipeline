"""Fixtures for the control panel tests. Nothing here touches the real .env,
secrets/, Voicebox, Anthropic or YouTube: every path is a temp directory, the
process spawner is a stub and Voicebox is a fake callable."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading

import pytest

from newsletter_video import characters
from newsletter_video.ui import server as server_mod
from newsletter_video.ui.config import UIPaths

DUMMY_SECRET = "sk-ant-DUMMY-not-a-real-key-123456"
WEBHOOK_SECRET = "https://hooks.example.test/T000/B000/dummywebhooksecret"
ENV_TEXT = f"""# Pipeline settings (test copy)
ANTHROPIC_API_KEY={DUMMY_SECRET}
TYPESAFE_API_KEY=ts-dummy-secret-654321
NOTIFY_WEBHOOK_URL={WEBHOOK_SECRET}
TTS_PROVIDER=kokoro   # inline comment
VOICEBOX_PROFILE_ID=abc12345
VIDEOS_PER_DAY=3

# keep me
YOUTUBE_UPLOAD=0
YOUTUBE_PRIVACY=private
UNRELATED_SETTING=hello world
"""
SECRETS = [DUMMY_SECRET, "ts-dummy-secret-654321", WEBHOOK_SECRET]


@pytest.fixture
def paths(tmp_path, monkeypatch) -> UIPaths:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "output").mkdir()
    env = root / ".env"
    env.write_text(ENV_TEXT)
    env.chmod(0o600)
    (root / "assets" / "characters").mkdir(parents=True)
    (root / "assets" / "character").mkdir(parents=True)
    (root / "newsletter_video").mkdir()
    monkeypatch.setattr(characters, "CHARACTERS_DIR", root / "assets" / "characters")
    return UIPaths.under(root, launchd_plist=root / "daily.plist")


def make_character(paths: UIPaths, slug="gizmo", name="Gizmo", voice=True, persona="chirpy"):
    from PIL import Image

    folder = paths.characters_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (40, 60), (255, 0, 0, 0)).save(folder / "avatar.png")
    manifest = {"name": name, "avatar": "avatar.png", "persona": persona}
    if voice:
        manifest["voicebox_profile_id"] = "vb-profile-1234"
    (folder / "character.json").write_text(json.dumps(manifest))
    return folder


class FakeSpawner:
    """Stands in for the real supervisor: records the call, reports a live pid."""

    def __init__(self):
        self.calls: list[dict] = []
        self.pid = os.getpid()

    def __call__(self, argv, env, job_dir, cwd):
        self.calls.append({"argv": argv, "env": env, "job_dir": job_dir, "cwd": cwd})
        return self.pid


class FakeVoicebox:
    def __init__(self):
        self.up = True
        self.profiles = [{"id": "p1", "name": "Narrator Nina"}, {"id": "p2", "name": "Bob"}]

    def __call__(self, path):
        if not self.up:
            raise OSError("down")
        if path == "/health":
            return {"status": "ok"}
        if path == "/profiles":
            return self.profiles
        raise OSError(path)


class Client:
    def __init__(self, port: int, token: str):
        self.port, self.token = port, token

    def request(self, method, path, body=None, headers=None, json_body=None, token=True, host=None,
                content_type=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if content_type:
            hdrs["Content-Type"] = content_type
        if token:
            hdrs["X-UI-Token"] = self.token
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        for k, v in hdrs.items():
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def get_json(self, path, **kw):
        status, _, data = self.request("GET", path, **kw)
        return status, json.loads(data or b"{}")

    def send_json(self, method, path, payload, **kw):
        status, _, data = self.request(method, path, json_body=payload, **kw)
        return status, json.loads(data or b"{}")


@pytest.fixture
def spawner():
    return FakeSpawner()


@pytest.fixture
def voicebox():
    return FakeVoicebox()


@pytest.fixture
def running(paths, spawner, voicebox):
    """A live server on a random port, wired to the stubs above."""
    server, app = server_mod.make_server(paths, port=0, spawner=spawner, lister=lambda: [], voicebox=voicebox)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    yield app, Client(app.guard.port, app.guard.token)
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(running):
    return running[1]


@pytest.fixture
def app(running):
    return running[0]


@pytest.fixture
def sleeper():
    """A real, harmless long-running process-group leader for cancel tests."""
    procs = []

    def start():
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        procs.append(p)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait()


def multipart(fields: dict, files: list[tuple[str, str, bytes]]):
    boundary = "----testboundary7MA4YWxkTrZu0gW"
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, filename, data in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"
