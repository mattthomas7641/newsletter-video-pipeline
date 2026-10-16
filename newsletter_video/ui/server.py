"""The control panel's HTTP server (standard library only).

`App.handle` is a pure request -> response function, so it can be tested
without sockets; `make_server` wires it into ThreadingHTTPServer on 127.0.0.1.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import history, security
from .characters_svc import CharacterService
from .config import PROJECT_ROOT, UIPaths
from .envstore import SCHEMA, EnvStore, SettingsError
from .jobs import JobManager, Spawner, default_spawner
from .runs import RunBlocked, RunService

log = logging.getLogger("newsletter_video.ui")
STATIC_DIR = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                "style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict  # case-insensitive mapping (http.server's Message) or a plain dict with canonical names
    body: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name) or default


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)
    file: Path | None = None  # streamed from disk, with Range support
    file_type: str | None = None


def json_response(data, status: int = 200) -> Response:
    return Response(status, json.dumps(data).encode(), "application/json; charset=utf-8",
                    {"Cache-Control": "no-store"})


def error(status: int, message: str, **extra) -> Response:
    return json_response({"error": message, **extra}, status)


def feature_support(root: Path) -> dict[str, bool]:
    """Which settings the installed pipeline code actually reads. Settings that
    aren't read yet are still written, and the UI says so."""
    sources = ""
    package = root / "newsletter_video"
    if package.is_dir():
        for path in package.glob("*.py"):
            try:
                sources += path.read_text(errors="ignore")
            except OSError:
                continue
    return {key: key in sources for key in SCHEMA}


class App:
    def __init__(self, paths: UIPaths, guard: security.Guard, spawner: Spawner = default_spawner,
                 lister=None, voicebox=None):
        self.paths = paths
        self.guard = guard
        self.store = EnvStore(paths.env_path)
        self.jobs = JobManager(paths.state_dir, paths.root, spawner=spawner,
                               redactor=lambda: self.store.secret_values())
        kwargs = {"lister": lister} if lister else {}
        self.runs = RunService(paths, self.jobs, self.store, **kwargs)
        self.characters = CharacterService(paths, self.jobs, self.store, **({"voicebox": voicebox} if voicebox else {}))
        self.support = feature_support(paths.root)
        self._schedule_cache: tuple[float, dict] | None = None
        self.index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # --- dispatch ---

    def handle(self, req: Request) -> Response:
        parts = urlsplit(req.path)
        path = parts.path
        is_api = path.startswith("/api/") or path.startswith("/media/")
        rejection = self.guard.check(req.method, req.headers, api=is_api)
        if rejection:
            return error(*rejection)
        try:
            if req.method in ("GET", "HEAD"):
                return self._get(path, req)
            if req.method in ("POST", "PUT"):
                return self._write(path, req)
            return error(405, "Method not allowed.")
        except RunBlocked as exc:
            return error(409, str(exc))
        except SettingsError as exc:
            return error(400, "Some settings aren't valid.", fields=exc.errors)
        except security.UploadError as exc:
            return error(400, str(exc))
        except KeyError:
            return error(404, "Not found.")
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error for %s %s", req.method, path)
            return error(500, "Something went wrong in the control panel. Details are in the terminal.")

    # --- GET ---

    def _get(self, path: str, req: Request) -> Response:
        if path == "/":
            html = self.index_html.replace("{{TOKEN}}", self.guard.token)
            cookie = f"{security.COOKIE_NAME}={self.guard.token}; Path=/; HttpOnly; SameSite=Strict"
            return Response(200, html.encode(), "text/html; charset=utf-8",
                            {"Set-Cookie": cookie, "Cache-Control": "no-store"})
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/settings":
            return json_response(self._settings())
        if path == "/api/characters":
            return json_response({"characters": self.characters.list()})
        if path == "/api/voicebox/profiles":
            return json_response(self.characters.voicebox_profiles())
        if path == "/api/setup":
            vb = self.characters.voicebox_status()["running"]
            return json_response({"checks": history.setup_check(self.paths, self.store, vb),
                                  "schedule": self._schedule()})
        if path == "/api/runs":
            return json_response(self._runs())
        if path == "/api/videos":
            today = date.today()
            return json_response({"today": history.day_summary(self.paths, today),
                                  "days": [d for d in history.recent_days(self.paths, today) if d["date"] != today.isoformat()],
                                  "previews": history.previews(self.paths)})
        m = re.fullmatch(r"/api/jobs/([a-z0-9]+)", path)
        if m:
            return self._job(m.group(1), int((req.query.get("since") or ["0"])[0] or 0))
        m = re.fullmatch(r"/media/video/(\d{4}-\d{2}-\d{2})/([^/]+)", path)
        if m:
            return self._media(self.paths.output_dir / m.group(1), unquote(m.group(2)), "video/mp4")
        m = re.fullmatch(r"/media/preview/([a-z0-9]+)/([^/]+)", path)
        if m and security.JOB_ID.match(m.group(1)):
            return self._media(self.paths.output_dir / "_previews" / m.group(1), unquote(m.group(2)), "video/mp4")
        m = re.fullmatch(r"/media/avatar/([^/]+)", path)
        if m:
            avatar = self.characters.avatar_path(unquote(m.group(1)))
            if not avatar:
                return error(404, "No avatar.")
            ctype = mimetypes.guess_type(avatar.name)[0] or "application/octet-stream"
            return Response(200, content_type=ctype, file=avatar, headers={"Cache-Control": "max-age=300"})
        m = re.fullmatch(r"/media/sample/([^/]+)", path)
        if m:
            sample = self.characters.sample_path(unquote(m.group(1)))
            if not sample:
                return error(404, "No sample.")
            return Response(200, content_type="audio/wav", file=sample)
        return error(404, "Not found.")

    def _static(self, rel: str) -> Response:
        target = (STATIC_DIR / unquote(rel)).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file() or target.name == "index.html":
            return error(404, "Not found.")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        return Response(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""),
                        {"Cache-Control": "no-cache"})

    def _media(self, folder: Path, name: str, ctype: str) -> Response:
        if not security.VIDEO_NAME.match(name):
            return error(404, "Not found.")
        target = (folder / name)
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            return error(404, "That video is no longer on disk.")
        base = self.paths.output_dir.resolve()
        if base not in resolved.parents or not resolved.is_file():
            return error(404, "Not found.")
        return Response(200, content_type=ctype, file=resolved)

    # --- payloads ---

    def _settings(self) -> dict:
        data = self.store.read()
        return {**data, "supported": self.support,
                "choices": {k: list(f.choices) for k, f in SCHEMA.items() if f.choices},
                "keys": self.store.secret_presence()}

    def _runs(self) -> dict:
        activity = [a.to_json() for a in self.runs.activity()]
        latest = self.runs.describe(self.runs.latest())
        return {"activity": activity, "latest": latest, "schedule": self._schedule()}

    def _schedule(self) -> dict:
        """`launchctl list` is comparatively slow, so cache it briefly."""
        now = time.monotonic()
        if self._schedule_cache is None or now - self._schedule_cache[0] > 30:
            self._schedule_cache = (now, history.schedule(self.paths))
        return self._schedule_cache[1]

    def _job(self, job_id: str, since: int) -> Response:
        view = self.jobs.get(job_id)
        if view is None:
            return error(404, "That job isn't known (it may be from an older session).")
        data = self.runs.describe(view) if view.kind in ("test", "batch") else view.to_json()
        payload = {"job": data, "log": self.jobs.log(job_id, since)}
        if view.kind == "add" and view.state != "running":
            payload["outcome"] = self.characters.add_outcome(view)
        if view.kind == "sample":
            payload["job"]["url"] = view.meta.get("url") if view.state == "done" else None
            payload["job"]["error_tail"] = self.jobs.tail(job_id, 4) if view.state in ("failed", "interrupted") else []
        return json_response(payload)

    # --- POST / PUT ---

    def _read_json(self, req: Request) -> dict:
        if len(req.body) > security.MAX_JSON_BYTES:
            raise security.UploadError("That request is too large.")
        if not req.body:
            return {}
        try:
            data = json.loads(req.body)
        except ValueError:
            raise security.UploadError("The request wasn't valid JSON.") from None
        if not isinstance(data, dict):
            raise security.UploadError("The request must be a JSON object.")
        return data

    def _write(self, path: str, req: Request) -> Response:
        if path == "/api/settings" and req.method == "PUT":
            updates = self._read_json(req).get("updates")
            if not isinstance(updates, dict) or not updates:
                return error(400, "Nothing to save.")
            self._check_character(updates)
            changed = self.store.write(updates)
            return json_response({"changed": changed, **self._settings()})
        if path == "/api/runs" and req.method == "POST":
            data = self._read_json(req)
            kind = data.get("kind")
            if kind == "test":
                overrides = data.get("overrides") or {}
                if not isinstance(overrides, dict):
                    return error(400, "overrides must be an object.")
                self._check_character(overrides)
                job = self.runs.start_test(overrides)
            elif kind == "batch":
                if data.get("confirm") is not True:
                    return error(400, "Running the daily batch needs confirmation.")
                job = self.runs.start_batch()
            else:
                return error(400, "Unknown run type.")
            return json_response({"job": self.runs.describe(job)}, 201)
        if path == "/api/characters" and req.method == "POST":
            return self._add_character(req)
        m = re.fullmatch(r"/api/characters/([^/]*)/sample", path)
        if m and req.method == "POST":
            return json_response(self.characters.start_sample(unquote(m.group(1))), 202)
        m = re.fullmatch(r"/api/jobs/([a-z0-9]+)/cancel", path)
        if m and req.method == "POST":
            return json_response({"cancelled": self.jobs.cancel(m.group(1))})
        return error(404, "Not found.")

    def _check_character(self, values: dict) -> None:
        slug = values.get("CHARACTER")
        if slug in (None, ""):
            return
        if slug not in {c["slug"] for c in self.characters.list()}:
            raise SettingsError({"CHARACTER": "that character doesn't exist"})

    def _add_character(self, req: Request) -> Response:
        if len(req.body) > security.MAX_BODY_BYTES:
            return error(413, "That upload is too large.")
        parts = security.parse_multipart(req.header("Content-Type"), req.body)
        fields = {p.name: p.data.decode("utf-8", "replace") for p in parts if p.filename is None}
        avatar = next((p.data for p in parts if p.name == "avatar" and p.filename is not None), b"")
        clips = [(p.filename or "", p.data) for p in parts if p.name == "voice" and p.filename]
        job = self.characters.start_add(fields, avatar, clips)
        return json_response({"job": job.to_json()}, 202)


# --- HTTP plumbing -------------------------------------------------------------

def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ByteSizePanel"

        def log_message(self, fmt, *args):  # quiet, and never log query strings
            path = urlsplit(self.path).path
            if path.startswith("/api/jobs/") or path.startswith("/media/"):
                return
            sys.stderr.write(f"  {self.command} {path} -> {args[1] if len(args) > 1 else ''}\n")

        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            limit = security.MAX_BODY_BYTES if self.command in ("POST", "PUT") else 0
            if length > limit:
                self._send(error(413, "That request is too large."))
                self.close_connection = True
                return
            body = self.rfile.read(length) if length else b""
            parts = urlsplit(self.path)
            req = Request(self.command, self.path, parse_qs(parts.query), self.headers, body)
            self._send(app.handle(req))

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = _dispatch

        def _send(self, resp: Response):
            headers = {**SECURITY_HEADERS, **resp.headers}
            if resp.file is not None:
                self._send_file(resp, headers)
                return
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        def _send_file(self, resp: Response, headers: dict):
            try:
                size = resp.file.stat().st_size
            except OSError:
                self._send(error(404, "That file is gone."))
                return
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    else:
                        start = max(0, size - int(m.group(2)))
                    end = min(end, size - 1)
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
            self.send_response(status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with resp.file.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser stopped reading (seeking in a video)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(paths: UIPaths, port: int = 8765, spawner: Spawner = default_spawner,
                **app_kwargs) -> tuple[Server, App]:
    guard = security.Guard(port=0, token=security.new_token())
    try:
        server = Server(("127.0.0.1", port), BaseHTTPRequestHandler)
    except OSError:
        server = Server(("127.0.0.1", 0), BaseHTTPRequestHandler)  # preferred port busy: take any free one
    guard.port = server.server_address[1]
    app = App(paths, guard, spawner=spawner, **app_kwargs)
    server.RequestHandlerClass = make_handler(app)
    return server, app
