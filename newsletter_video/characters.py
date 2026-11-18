"""Swappable narrators. A character is an avatar image plus (optionally) a
cloned Voicebox voice and a short writing persona, stored in
assets/characters/<slug>/ with a character.json manifest.

    python -m newsletter_video.characters add "Robo" --avatar robo.png --voice-audio robo.mp3 --use
    python -m newsletter_video.characters list
    python -m newsletter_video.characters say robo "Testing one two three"
    python -m newsletter_video.characters use robo     # or: use none
    python -m newsletter_video.characters poses puck neutral.png point.png think.png shrug.png \
        --roles neutral,point,think,shrug
    python -m newsletter_video.characters add-pose puck present.png --role present

A character's pictures are its main avatar plus extra poses, all on one canvas so they line up when the
video swaps them. Each has a ROLE (character.json "pose_roles", aligned with [avatar] + poses): neutral,
point, think, present, shrug, or any other word. The videos pick poses by role: point at charts and
numbers, think at a twist or a question, neutral to open, and the shrug ONLY in the last scene (the closing
question). A new pose added with add-pose simply joins the rotation.

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
    tts: str | None = None  # "kokoro" = a built-in Kokoro voice (kokoro_voice), no Voicebox clone needed
    kokoro_voice: str | None = None
    poses: tuple[Path, ...] = ()  # more cutouts of the same character; the video swaps between them (avatar is the first)
    pose_roles: tuple[str, ...] = ()  # one role per picture: [avatar] + poses ("neutral", "point", "shrug"...)

    def pictures(self) -> list[Path]:
        return ([self.avatar] if self.avatar else []) + list(self.poses)


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
    poses = []
    for name in data.get("poses") or []:
        pose = directory / str(name)
        if not pose.exists():
            raise RuntimeError(f"Character {slug!r} lists the pose {name!r} but the file is missing.")
        poses.append(pose)
    roles = pose_roles_for(data, 1 + len(poses) if avatar is not None else len(poses))
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
        tts=data.get("tts") or None,
        kokoro_voice=data.get("kokoro_voice") or None,
        poses=tuple(poses),
        pose_roles=tuple(roles),
    )


# --- pose roles ------------------------------------------------------------------

SHRUG = "shrug"
KNOWN_ROLES = ("neutral", "point", "think", "present", "shrug")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")


def default_roles(n: int) -> list[str]:
    """Roles for a character whose manifest names none: the main picture is neutral, the others are
    anonymous extras (never the shrug, so no picture is wrongly kept for the last scene)."""
    return (["neutral"] + [f"pose{k}" for k in range(2, n + 1)])[:n]


def pose_roles_for(manifest: dict, n: int) -> list[str]:
    roles = manifest.get("pose_roles")
    if isinstance(roles, list) and len(roles) == n and all(isinstance(r, str) and _ROLE_RE.match(r) for r in roles):
        return list(roles)
    return default_roles(n)


# What a scene's graphic or moment calls for, most fitting first (the shrug is never in these lists: it is
# reserved for the last scene).
STYLE_ROLES = {
    "hook": ("neutral", "present", "point"),
    "kinetic": ("neutral", "present", "point"),
    "stat": ("point", "present", "think"),
    "chart": ("point", "present", "think"),
    "timeline": ("think", "point", "present"),
    "versus": ("present", "point", "think"),
    "relation": ("present", "point", "neutral"),
    "list": ("present", "point", "neutral"),
    "quote": ("think", "present", "neutral"),
    "myth": ("think", "point", "present"),
    "question": ("think", "present", "neutral"),
    "beat": ("point", "present", "think", "neutral"),
}


def plan_poses(roles: list[str], n_scenes: int, wanted: list | None = None, kinds: list[str] | None = None,
               seed: str = "") -> list[int]:
    """One picture index per scene. Rules: the shrug (role "shrug") only in the LAST scene, and there
    whenever the character has one; never the same picture twice in a row; otherwise the scene's own
    choice (`wanted`: a role name, or a legacy picture index) when it is allowed, else the best fit for the
    scene's kind (`kinds`: the scene style, "hook", "question"...) among the poses not just used, with a
    seeded tie-break so a re-render looks the same. Any other pose (a new one) joins the rotation."""
    import random

    if n_scenes <= 0 or not roles:
        return []
    wanted = list(wanted or []) + [None] * n_scenes
    kinds = list(kinds or []) + ["beat"] * n_scenes
    shrug = roles.index(SHRUG) if SHRUG in roles else None
    normal = [k for k, r in enumerate(roles) if k != shrug] or [0]
    rng = random.Random(f"poses:{seed}")
    out: list[int] = []
    for i in range(n_scenes):
        last = i == n_scenes - 1 and n_scenes > 1
        prev = out[-1] if out else None
        if last and shrug is not None:
            out.append(shrug)
            continue
        w = wanted[i]
        pick = None
        if isinstance(w, str) and w.strip().lower() in roles:
            pick = roles.index(w.strip().lower())
        elif isinstance(w, int) and not isinstance(w, bool) and 0 <= w < len(roles):
            pick = w
        if pick is not None and (pick == shrug or pick == prev):
            pick = None  # a shrug before the end, or a repeat: choose another
        if pick is None:
            options = [k for k in normal if k != prev] or normal
            prefs = STYLE_ROLES.get(str(kinds[i]), STYLE_ROLES["beat"])
            ranked = [k for r in prefs for k in options if roles[k] == r]
            if i == 0 and 0 in options and not isinstance(w, (str, int)):
                ranked = [0] + ranked  # open on the main picture
            others = [k for k in options if k not in ranked]
            rng.shuffle(others)
            fresh = [k for k in others if roles[k] not in KNOWN_ROLES]
            if fresh and ranked and rng.random() < 0.4:  # a pose with a role of its own: work it into the rotation
                ranked.insert(1 if i == 0 else 0, fresh[0])
            # ...preferring one not used in the last two scenes, so it doesn't ping-pong between two poses
            pick = sorted(ranked + others, key=lambda k: k in out[-2:])[0]
        out.append(pick)
    return out


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


def _check_roles(roles: list[str] | None, n: int) -> list[str]:
    if roles is None:
        return default_roles(n)
    roles = [r.strip().lower() for r in roles]
    if len(roles) != n:
        raise SystemExit(f"Give one role per image ({n} images, {len(roles)} roles).")
    bad = [r for r in roles if not _ROLE_RE.match(r)]
    if bad:
        raise SystemExit(f"Roles are short lower-case words (neutral, point, think, present, shrug...), not {bad}")
    if len(set(roles)) != len(roles):
        raise SystemExit(f"Each role once: {roles}")
    return roles


def set_poses(slug: str, sources: list[Path], roles: list[str] | None = None) -> Character:
    """Replaces a character's pictures with these poses, cut out and aligned: the first becomes the main
    avatar, the rest are the other poses the video swaps to. They must share one canvas size. The shared
    crop box and the source canvas size are stored so add_pose can line a later pose up exactly."""
    from .cutout import cut_poses_with_canvas

    character = load(slug)
    for src in sources:
        if not src.exists():
            raise SystemExit(f"Pose image not found: {src}")
    roles = _check_roles(roles, len(sources))
    images, canvas = cut_poses_with_canvas(sources)
    for old in character.directory.glob("pose-*.png"):
        old.unlink()
    images[0].save(character.directory / "avatar.png", "PNG")
    names = []
    for k, image in enumerate(images[1:], start=2):
        image.save(character.directory / f"pose-{k}.png", "PNG")
        names.append(f"pose-{k}.png")
    manifest_path = character.directory / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest["avatar"] = "avatar.png"
    manifest["poses"] = names
    manifest["pose_roles"] = roles
    manifest["canvas"] = canvas
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return load(slug)


def _paste_onto(image, size: tuple[int, int], offset: tuple[int, int]):
    from PIL import Image

    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(image, offset)  # no mask: onto a transparent canvas the alpha must be copied, not multiplied
    return out


def _align_by_body(new, ref):
    """Fallback when the existing poses' canvas is unknown (made before it was stored): scale the new
    cutout so its figure is as tall as the main pose's, stand it on the same floor line and centre its legs
    (the bottom 30% of the figure, which barely moves between poses) on the main pose's legs.
    Returns (scaled cutout cropped to its figure, its x, y on the main pose's canvas)."""
    import numpy as np

    def legs_centre(img):
        a = np.asarray(img.getchannel("A")) > 8
        ys, xs = np.nonzero(a)
        top, bottom = ys.min(), ys.max()
        band = ys >= bottom - 0.3 * (bottom - top)
        return float(xs[band].mean()), int(top), int(bottom)

    nbox = new.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    new = new.crop(nbox)
    rcx, rtop, rbottom = legs_centre(ref)
    scale = (rbottom - rtop + 1) / new.height
    new = new.resize((max(1, round(new.width * scale)), max(1, round(new.height * scale))))
    ncx, _, nbottom = legs_centre(new)
    return new, round(rcx - ncx), round(rbottom - nbottom)


def add_pose(slug: str, image: Path, role: str) -> Character:
    """Adds ONE pose to a character without redoing the others. The new image is cut out at full size; with
    the canvas stored by set_poses (same source size) it is cropped with the very same box, and when the new
    pose reaches outside that box (an arm held out) every picture is padded to the grown box, so all of them
    still line up. Without a stored canvas it is lined up by its body (scaled to the main pose's height,
    feet on the same floor line, legs centred) and the result is printed for checking."""
    from PIL import Image

    from .cutout import _box, cutout, union_box

    character = load(slug)
    if not image.exists():
        raise SystemExit(f"Pose image not found: {image}")
    role = _check_roles([role], 1)[0]
    if role in character.pose_roles:
        raise SystemExit(f"{slug!r} already has a {role!r} pose ({', '.join(character.pose_roles)}).")
    manifest_path = character.directory / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    pictures = character.pictures()
    if not pictures:
        raise SystemExit(f"{slug!r} has no avatar yet; add one first.")
    existing = [Image.open(p).convert("RGBA") for p in pictures]
    cut = cutout(Image.open(image))
    canvas = manifest.get("canvas") or {}
    if canvas and list(cut.size) == list(canvas.get("source_size") or []):
        box, scale = tuple(canvas["box"]), float(canvas.get("scale") or 1.0)
        nbox = _box(cut) or box
        union = union_box([box, nbox])
        size = (round((union[2] - union[0]) * scale), round((union[3] - union[1]) * scale))
        offset = (round((box[0] - union[0]) * scale), round((box[1] - union[1]) * scale))
        if union != box:
            existing = [_paste_onto(img, size, offset) for img in existing]
        new = cut.crop(union).resize(size) if scale != 1.0 else cut.crop(union)
        manifest["canvas"] = {**canvas, "box": list(union)}
        how = "lined up with the stored canvas box" + (" (box grown: every pose padded)" if union != box else "")
    else:
        if canvas:
            print(f"  ! {image.name} is {cut.size[0]}x{cut.size[1]}, not the {canvas.get('source_size')} canvas the "
                  "other poses were cut from; lining it up by its body instead.")
        piece, x, y = _align_by_body(cut, existing[0])
        w, h = existing[0].size
        left, top = min(0, x), min(0, y)
        right, bottom = max(w, x + piece.width), max(h, y + piece.height)
        size = (right - left, bottom - top)
        if size != (w, h):
            existing = [_paste_onto(img, size, (-left, -top)) for img in existing]
        new = _paste_onto(piece, size, (x - left, y - top))
        how = "lined up by body (no stored canvas): check it with `characters list` and a preview"
    for img, path in zip(existing, pictures):
        if img.size != Image.open(path).size:
            img.save(path, "PNG")
    k = len(pictures) + 1
    while (character.directory / f"pose-{k}.png").exists():
        k += 1
    name = f"pose-{k}.png"
    new.save(character.directory / name, "PNG")
    manifest["poses"] = list(manifest.get("poses") or []) + [name]
    manifest["pose_roles"] = list(character.pose_roles) + [role]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  added {name} as {role!r}: {how}")
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

    p_poses = sub.add_parser("poses", help="give a character several poses the video swaps between (first = main)")
    p_poses.add_argument("name")
    p_poses.add_argument("images", nargs="+", type=Path, help="same-size images on a plain background; the first is the main pose")
    p_poses.add_argument("--roles", help="comma-separated role per image, e.g. neutral,point,think,shrug")

    p_addpose = sub.add_parser("add-pose", help="add one more pose to a character, lined up with its others")
    p_addpose.add_argument("name")
    p_addpose.add_argument("image", type=Path)
    p_addpose.add_argument("--role", required=True, help="neutral, point, think, present, shrug, or another word")

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
            voice = (f"Kokoro {c.kokoro_voice}" if c.tts == "kokoro" else f"voice {c.voicebox_profile_id[:8]}" if c.voicebox_profile_id else "default voice")
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
    elif args.cmd == "poses":
        roles = [r for r in args.roles.split(",")] if args.roles else None
        character = set_poses(args.name, args.images, roles)
        print(f"{character.name} now has {1 + len(character.poses)} poses: {', '.join(character.pose_roles)}.")
    elif args.cmd == "add-pose":
        character = add_pose(args.name, args.image, args.role)
        print(f"{character.name} now has {len(character.pose_roles)} poses: {', '.join(character.pose_roles)}.")
    elif args.cmd == "say":
        load(args.name)
        print(f"Wrote {_say(args.name, args.text)}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
