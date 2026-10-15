"""Request guards and upload validation.

The server only listens on 127.0.0.1, but a web page you visit can still try
to talk to it (cross-site requests, DNS rebinding). Guards, in order:

1. Host must be the loopback address with our port (defeats DNS rebinding).
2. Origin (when sent) and Sec-Fetch-Site must be same-origin (defeats CSRF).
3. State-changing requests need the per-launch token in `X-UI-Token`; reads of
   API data need that header or the matching same-site cookie.
4. State-changing requests must be JSON or multipart, never a simple
   cross-site form post.
"""
from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP
from typing import Mapping

TOKEN_HEADER = "X-UI-Token"
COOKIE_NAME = "bytesize_ui"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class Guard:
    port: int
    token: str

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self.allowed_hosts}

    def check(self, method: str, headers: Mapping[str, str], *, api: bool) -> tuple[int, str] | None:
        """Returns (status, message) to reject with, or None to allow.
        `headers` lookups must be case-insensitive (http.server's are)."""
        host = (headers.get("Host") or "").lower()
        if host not in self.allowed_hosts:
            return 403, "Unexpected Host header. Open the panel from the address it printed."
        origin = headers.get("Origin")
        if origin is not None and origin not in self.allowed_origins:
            return 403, "Cross-origin request blocked."
        if headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return 403, "Cross-site request blocked."
        if not api:
            return None
        supplied = headers.get(TOKEN_HEADER) or ""
        if method in SAFE_METHODS:
            supplied = supplied or _cookie(headers.get("Cookie") or "", COOKIE_NAME)
        else:
            ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json" and ctype != "multipart/form-data" and ctype != "":
                return 415, "Send JSON or a multipart upload."
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return 403, "Missing or wrong session token. Reload the page."
        return None


def _cookie(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


# --- names -----------------------------------------------------------------

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VIDEO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.mp4$")
JOB_ID = re.compile(r"^[a-z0-9]{6,40}$")


def is_slug(value: str) -> bool:
    return bool(_SLUG.match(value or ""))


def clean_display_name(raw: str, limit: int = 40) -> str:
    """A character name for display: printable, single-line, no markup characters."""
    text = re.sub(r"[\x00-\x1f\x7f<>]", " ", raw or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def clean_persona(raw: str, limit: int = 400) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", raw or "")
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


# --- uploads ---------------------------------------------------------------

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_CLIPS = 5
MAX_BODY_BYTES = 70 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024


class UploadError(ValueError):
    pass


def sniff_image(data: bytes) -> str:
    """Image kind from the bytes, not the filename. Returns 'png', 'jpeg' or 'webp'."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise UploadError("The avatar must be a PNG, JPEG or WebP image.")


def validate_image(data: bytes) -> dict:
    """Checks type by content, decodes it with Pillow, and reports transparency."""
    if not data:
        raise UploadError("The avatar file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError(f"The avatar is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    kind = sniff_image(data)
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            rgba = img.convert("RGBA")
            transparent = rgba.getchannel("A").getextrema()[0] < 255
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise UploadError("That image could not be read. Try exporting it again as a PNG.") from exc
    if width * height > 40_000_000:
        raise UploadError("That image is too large (over 40 megapixels).")
    return {"kind": kind, "width": width, "height": height, "has_transparency": transparent}


def sniff_audio(data: bytes) -> str | None:
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    return None


def validate_audio(data: bytes, label: str = "voice clip") -> str:
    if not data:
        raise UploadError(f"The {label} is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise UploadError(f"The {label} is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
    kind = sniff_audio(data)
    if kind is None:
        raise UploadError(f"The {label} isn't a recognised audio file (use WAV, MP3, M4A, OGG or FLAC).")
    return kind


@dataclass
class FormPart:
    name: str
    filename: str | None
    data: bytes


def parse_multipart(content_type: str, body: bytes) -> list[FormPart]:
    """Parses multipart/form-data with the stdlib email parser. The client's
    filenames are only kept for display; nothing here is used as a path."""
    if not content_type.lower().startswith("multipart/form-data"):
        raise UploadError("Expected a multipart upload.")
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise UploadError("The upload was malformed.")
    parts = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        parts.append(FormPart(str(name), str(filename) if filename else None, payload))
    return parts
