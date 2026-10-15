"""Swappable narrators. A character is an avatar image plus (optionally) a
cloned Voicebox voice and a short writing persona, stored in
assets/characters/<slug>/ with a character.json manifest.

    python -m newsletter_video.characters add "SpongeBob" --avatar sb.png --voice-audio sb.mp3 --use
    python -m newsletter_video.characters list
    python -m newsletter_video.characters say spongebob "Testing one two three"
    python -m newsletter_video.characters use spongebob     # or: use none

CHARACTER=<slug> in .env selects the narrator. With it unset, the pipeline
behaves as before (assets/character/ image + VOICEBOX_PROFILE_ID).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHARACTERS_DIR = PROJECT_ROOT / "assets" / "characters"
MANIFEST = "character.json"
ENV_KEY = "CHARACTER"
VOICEBOX_URL = "http://localhost:17493"
CLEAN_CLIP_SECONDS = (3.0, 30.0)  # outside this, voice clones get noticeably worse


@dataclass
class Character:
    slug: str
    name: str
    directory: Path
    avatar: Path | None
    voicebox_profile_id: str | None = None
    engine: str | None = None
    model_size: str | None = None
    instruct: str | None = None
    persona: str = ""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load(slug: str) -> Character:
    directory = CHARACTERS_DIR / slug
    manifest = directory / MANIFEST
    if not manifest.exists():
        available = ", ".join(c.slug for c in list_characters()) or "none yet"
        raise RuntimeError(
            f"CHARACTER={slug!r} but there's no assets/characters/{slug}/{MANIFEST} "
            f"(available: {available}). Add one with `python -m newsletter_video.characters add`, "
            f"or unset CHARACTER."
        )
    data = json.loads(manifest.read_text())
    avatar = directory / data["avatar"] if data.get("avatar") else None
    if avatar is not None and not avatar.exists():
        raise RuntimeError(f"Character {slug!r} lists avatar {data['avatar']!r} but the file is missing.")
    return Character(
        slug=slug,
        name=data.get("name", slug),
        directory=directory,
        avatar=avatar,
        voicebox_profile_id=data.get("voicebox_profile_id"),
        engine=data.get("engine"),
        model_size=data.get("model_size"),
        instruct=data.get("instruct"),
        persona=data.get("persona", ""),
    )


def list_characters() -> list[Character]:
    if not CHARACTERS_DIR.is_dir():
        return []
    found = []
    for manifest in sorted(CHARACTERS_DIR.glob(f"*/{MANIFEST}")):
        try:
            found.append(load(manifest.parent.name))
        except RuntimeError:
            continue
    return found


def active() -> Character | None:
    """The CHARACTER selected in the environment, or None for the legacy
    setup. Read at call time (run_pipeline loads .env after importing)."""
    slug = os.environ.get(ENV_KEY, "").strip()
    return load(slug) if slug else None


# --- adding a character ---------------------------------------------------

def _prepare_avatar(src: Path, dst: Path) -> None:
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] == 255:
        print("  ! The avatar has no transparent background, so it will show as a rectangle. "
              "A cutout PNG (background removed) looks right.")
    else:
        box = alpha.getbbox()
        if box:
            img = img.crop(box)  # trim empty margins so sizing matches what's visible
    img.thumbnail((2000, 2000))
    img.save(dst, "PNG")


def _to_wav(src: Path, dst: Path) -> float:
    import imageio_ffmpeg
    import soundfile

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )
    info = soundfile.info(str(dst))
    return info.frames / info.samplerate


def _voicebox(method: str, path: str, **kwargs):
    import requests

    resp = requests.request(method, f"{VOICEBOX_URL}{path}", timeout=120, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"Voicebox {method} {path} failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _clone_voice(name: str, clips: list[Path], texts: list[str] | None) -> str:
    from .tts import _ensure_voicebox_running

    _ensure_voicebox_running()
    with tempfile.TemporaryDirectory() as tmp:
        prepared = []
        for i, clip in enumerate(clips):
            wav = Path(tmp) / f"clip{i}.wav"
            seconds = _to_wav(clip, wav)
            low, high = CLEAN_CLIP_SECONDS
            if not low <= seconds <= high:
                print(f"  ! {clip.name} is {seconds:.0f}s; clones work best with {low:.0f}-{high:.0f}s "
                      f"of clean, single-speaker speech (no music, laughs, or long pauses).")
            if texts and i < len(texts):
                text = texts[i]
            else:
                with wav.open("rb") as fh:
                    text = _voicebox("POST", "/transcribe", files={"file": (wav.name, fh, "audio/wav")})["text"]
                print(f"  transcript of {clip.name}: {text!r}")
            prepared.append((wav, text))

        profile = _voicebox("POST", "/profiles", json={"name": name, "voice_type": "cloned", "language": "en"})
        try:
            for wav, text in prepared:
                with wav.open("rb") as fh:
                    _voicebox("POST", f"/profiles/{profile['id']}/samples",
                              files={"file": (wav.name, fh, "audio/wav")}, data={"reference_text": text})
        except Exception:
            try:
                _voicebox("DELETE", f"/profiles/{profile['id']}")  # don't leave a half-built profile behind
            except Exception:
                pass
            raise
    return profile["id"]


def add(name: str, avatar: Path, voice_audio: list[Path], voice_text: list[str] | None,
        profile_id: str | None, persona: str, force: bool) -> Character:
    slug = slugify(name)
    if not slug:
        raise SystemExit("Give the character a name with letters or numbers in it.")
    directory = CHARACTERS_DIR / slug
    if (directory / MANIFEST).exists() and not force:
        raise SystemExit(f"{slug!r} already exists; use --force to replace it (its Voicebox voice is kept).")
    if not avatar.exists():
        raise SystemExit(f"Avatar image not found: {avatar}")
    for clip in voice_audio:
        if not clip.exists():
            raise SystemExit(f"Voice clip not found: {clip}")
    if profile_id and voice_audio:
        raise SystemExit("Use either --profile-id (an existing Voicebox voice) or --voice-audio, not both.")

    print(f"Adding {name!r} as {slug!r}")
    if profile_id:
        _voicebox("GET", f"/profiles/{profile_id}")  # fail now if it doesn't exist
    elif voice_audio:
        print("Cloning the voice in Voicebox...")
        profile_id = _clone_voice(name, voice_audio, voice_text)

    directory.mkdir(parents=True, exist_ok=True)
    _prepare_avatar(avatar, directory / "avatar.png")
    manifest = {"name": name, "avatar": "avatar.png"}
    if profile_id:
        manifest["voicebox_profile_id"] = profile_id
    if persona:
        manifest["persona"] = persona
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return load(slug)


# --- selecting the active character ------------------------------------------

def _set_env(value: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    line = f"{ENV_KEY}={value}"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    for i, existing in enumerate(lines):
        if existing.startswith(f"{ENV_KEY}="):
            lines[i] = line
            break
    else:
        lines += ["", "# Narrator: a folder name under assets/characters/ (blank = legacy single avatar)", line]
    env_path.write_text("\n".join(lines) + "\n")


def _say(slug: str, text: str) -> Path:
    from .tts import synthesize

    os.environ[ENV_KEY] = slug
    out = PROJECT_ROOT / "output" / "_character_tests" / f"{slug}.wav"
    synthesize(text, out)
    return out


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.characters", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show characters and which one is active")

    p_add = sub.add_parser("add", help="add a character (avatar image + optional cloned voice)")
    p_add.add_argument("name")
    p_add.add_argument("--avatar", required=True, type=Path, help="image file, ideally a transparent-background PNG")
    p_add.add_argument("--voice-audio", type=Path, action="append", default=[],
                       help="clean reference clip(s) of the voice; repeat for more clips")
    p_add.add_argument("--voice-text", action="append",
                       help="transcript of each --voice-audio clip, in order (default: auto-transcribed)")
    p_add.add_argument("--profile-id", help="use an existing Voicebox voice profile instead of cloning")
    p_add.add_argument("--persona", default="",
                       help='how the writer should voice them, e.g. "bubbly, endlessly upbeat, loves puns"')
    p_add.add_argument("--use", action="store_true", help="make it the active character")
    p_add.add_argument("--force", action="store_true")

    p_use = sub.add_parser("use", help="set the active character (or 'none')")
    p_use.add_argument("name")

    p_say = sub.add_parser("say", help="hear a character's voice without rendering a video")
    p_say.add_argument("name")
    p_say.add_argument("text", nargs="?", default="Here are today's top tech stories, and one of them is wild.")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        current = os.environ.get(ENV_KEY, "").strip()
        characters = list_characters()
        if not characters:
            print("No characters yet. Add one: python -m newsletter_video.characters add NAME --avatar IMG --voice-audio CLIP")
        for c in characters:
            voice = f"voice {c.voicebox_profile_id[:8]}" if c.voicebox_profile_id else "default voice"
            print(f"{'*' if c.slug == current else ' '} {c.slug:<20} {c.name:<20} {voice}")
        print(f"\nActive: {current or '(none: legacy assets/character/ + VOICEBOX_PROFILE_ID)'}")
    elif args.cmd == "add":
        character = add(args.name, args.avatar, args.voice_audio, args.voice_text, args.profile_id,
                        args.persona, args.force)
        if args.use:
            _set_env(character.slug)
        print(f"\nDone. Hear it:  python -m newsletter_video.characters say {character.slug}")
        print(f"Activate it:    python -m newsletter_video.characters use {character.slug}" if not args.use
              else "It's now the active character.")
    elif args.cmd == "use":
        if args.name.lower() == "none":
            _set_env("")
            print("Back to the legacy single avatar/voice.")
        else:
            load(args.name)  # validates
            _set_env(args.name)
            print(f"Active character: {args.name}")
    elif args.cmd == "say":
        load(args.name)
        print(f"Wrote {_say(args.name, args.text)}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
