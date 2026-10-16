"""Reads and writes the handful of non-secret `.env` settings the control
panel manages.

Safety properties (all covered by tests):
- Only keys in `SCHEMA` can be read back or written. Anything else in the
  file (API keys, tokens, other settings, comments, blank lines) is never
  returned by this module and is preserved byte-for-byte on write.
- Values are validated against the schema, so nothing needing quoting or
  escaping ever reaches the file.
- Writes are atomic (temp file in the same directory, fsync, rename) and keep
  the file's permission bits. A new file is created 0600.
- `secret_presence` reports only whether a secret key has a value.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class Field:
    key: str
    default: str
    check: Callable[[str], str | None]  # returns an error message, or None when valid
    choices: tuple[str, ...] = ()


def _one_of(*choices: str) -> Callable[[str], str | None]:
    return lambda v: None if v in choices else f"must be one of: {', '.join(choices)}"


def _int_between(low: int, high: int) -> Callable[[str], str | None]:
    def check(value: str) -> str | None:
        if not re.fullmatch(r"\d{1,3}", value) or not low <= int(value) <= high:
            return f"must be a whole number from {low} to {high}"
        return None

    return check


def _slug_or_blank(value: str) -> str | None:
    return None if value == "" or _SLUG.match(value) else "must be a character folder name or blank"


SCHEMA: dict[str, Field] = {f.key: f for f in [
    Field("VIDEO_STYLE", "avatar", _one_of("avatar", "explainer"), ("avatar", "explainer")),
    Field("VIDEO_THEME", "light", _one_of("light", "dark"), ("light", "dark")),
    Field("CHARACTER", "", _slug_or_blank),
    Field("BACKGROUND_STYLE", "gameplay", _one_of("gameplay", "none"), ("gameplay", "none")),
    Field("VIDEOS_PER_DAY", "3", _int_between(1, 20)),
    Field("YOUTUBE_UPLOAD", "0", _one_of("0", "1"), ("0", "1")),
    Field("YOUTUBE_PRIVACY", "private", _one_of("private", "unlisted", "public"),
          ("private", "unlisted", "public")),
    Field("FALLBACK_UPLOAD", "0", _one_of("0", "1"), ("0", "1")),
]}

# Keys the panel may only ever report as present/missing.
SECRET_KEYS = ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "ELEVENLABS_API_KEY", "NOTIFY_WEBHOOK_URL")
# Read-only context the panel may display (never written, never secret).
READ_ONLY_KEYS = ("TTS_PROVIDER",)
# Non-secret keys whose mere presence is reported.
PRESENCE_KEYS = ("VOICEBOX_PROFILE_ID",)
_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|WEBHOOK|CREDENTIAL)", re.IGNORECASE)


class SettingsError(ValueError):
    """A submitted setting is not allowed or not valid. `errors` maps key -> message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {m}" for k, m in errors.items()))
        self.errors = errors


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


class EnvStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    # --- reading ---

    def _lines(self) -> list[str]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return text.splitlines()

    def _all_pairs(self) -> dict[str, str]:
        """Every KEY=value in the file (last one wins, like python-dotenv).
        Private: callers outside this class only get allowlisted slices."""
        pairs: dict[str, str] = {}
        for line in self._lines():
            if line.lstrip().startswith("#"):
                continue
            match = _LINE.match(line)
            if match:
                pairs[match.group(1)] = _parse_value(match.group(2))
        return pairs

    def read(self) -> dict:
        """Allowlisted settings: {"values": {KEY: value}, "set": [keys present in the file],
        "invalid": {KEY: message}}. Missing or invalid values fall back to the default."""
        pairs = self._all_pairs()
        values, present, invalid = {}, [], {}
        for key, field in SCHEMA.items():
            if key in pairs:
                present.append(key)
                problem = field.check(pairs[key])
                if problem:
                    invalid[key] = problem
                    values[key] = field.default
                else:
                    values[key] = pairs[key]
            else:
                values[key] = field.default
        return {"values": values, "set": present, "invalid": invalid}

    def read_only(self) -> dict[str, str]:
        pairs = self._all_pairs()
        return {k: pairs.get(k, "") for k in READ_ONLY_KEYS}

    def has_value(self, key: str, environ: Mapping[str, str] | None = None) -> bool:
        if key not in SECRET_KEYS and key not in PRESENCE_KEYS:
            raise KeyError(key)
        environ = os.environ if environ is None else environ
        return bool(self._all_pairs().get(key) or environ.get(key))

    def secret_presence(self, environ: Mapping[str, str] | None = None) -> dict[str, bool]:
        pairs = self._all_pairs()
        environ = os.environ if environ is None else environ
        return {k: bool(pairs.get(k) or environ.get(k)) for k in SECRET_KEYS}

    def secret_values(self, environ: Mapping[str, str] | None = None) -> set[str]:
        """Values to scrub from anything shown in the browser. Never serialized."""
        environ = os.environ if environ is None else environ
        found = {v for k, v in self._all_pairs().items() if _SECRETISH.search(k)}
        found |= {v for k, v in environ.items() if _SECRETISH.search(k)}
        return {v for v in found if len(v) >= 6}

    # --- writing ---

    @staticmethod
    def validate(updates: Mapping[str, object]) -> dict[str, str]:
        errors, clean = {}, {}
        for key, raw in updates.items():
            field = SCHEMA.get(key)
            if field is None:
                errors[key] = "this setting can't be changed from the control panel"
                continue
            value = str(raw).strip() if not isinstance(raw, bool) else ("1" if raw else "0")
            problem = field.check(value)
            if problem:
                errors[key] = problem
            else:
                clean[key] = value
        if errors:
            raise SettingsError(errors)
        return clean

    def write(self, updates: Mapping[str, object]) -> list[str]:
        """Applies validated updates; returns the keys whose value actually changed."""
        clean = self.validate(updates)
        with self._lock:
            target = Path(os.path.realpath(self.path))
            try:
                raw = target.read_bytes().decode("utf-8")
                mode = target.stat().st_mode & 0o7777
            except FileNotFoundError:
                raw, mode = "", 0o600
            newline = "\r\n" if "\r\n" in raw else "\n"
            lines = raw.splitlines()

            last_index: dict[str, int] = {}
            for i, line in enumerate(lines):
                if line.lstrip().startswith("#"):
                    continue
                match = _LINE.match(line)
                if match and match.group(1) in clean:
                    last_index[match.group(1)] = i

            changed, appended = [], []
            for key, value in clean.items():
                if key in last_index:
                    i = last_index[key]
                    if _parse_value(_LINE.match(lines[i]).group(2)) != value:
                        prefix = "export " if lines[i].lstrip().startswith("export") else ""
                        lines[i] = f"{prefix}{key}={value}"
                        changed.append(key)
                else:
                    appended.append(f"{key}={value}")
                    changed.append(key)
            if not changed:
                return []
            if appended:
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append("# Added by the control panel")
                lines.extend(appended)
            self._atomic_write(target, newline.join(lines) + newline, mode)
            return changed

    @staticmethod
    def _atomic_write(target: Path, text: str, mode: int) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                os.fchmod(handle.fileno(), mode)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
