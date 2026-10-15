"""Background workers started by the control panel, each in its own process.

    python -m newsletter_video.ui.workers sample --slug SLUG|default --out FILE [--text TEXT]
    python -m newsletter_video.ui.workers add --spec FILE

They reuse the existing code paths (`tts.synthesize`, `characters.add`) and
report back through files and stdout, which the panel's job manager reads.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_TEXT = "Here are today's top tech stories, and one of them is wild."


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.environ.get("BYTESIZE_ENV_FILE") or PROJECT_ROOT / ".env")


def sample(args: argparse.Namespace) -> int:
    _load_env()
    # A character's voice is selected through CHARACTER, exactly as in a real run;
    # "default" means the original single voice from .env.
    os.environ["CHARACTER"] = "" if args.slug == "default" else args.slug
    from newsletter_video.tts import synthesize

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("Generating a short sample of the voice...", flush=True)
    synthesize(args.text, out)
    print("Done.", flush=True)
    return 0


def add(args: argparse.Namespace) -> int:
    _load_env()
    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text())
    outcome_path = spec_path.with_name("outcome.json")
    workdir = spec_path.parent

    def finish(ok: bool, message: str = "", slug: str | None = None) -> int:
        outcome_path.write_text(json.dumps({"ok": ok, "message": message, "slug": slug}))
        shutil.rmtree(workdir / "files", ignore_errors=True)  # uploaded originals aren't kept
        return 0 if ok else 1

    from newsletter_video import characters

    try:
        character = characters.add(
            name=spec["name"],
            avatar=Path(spec["avatar"]),
            voice_audio=[Path(p) for p in spec.get("voice_audio", [])],
            voice_text=None,
            profile_id=spec.get("profile_id") or None,
            persona=spec.get("persona", ""),
            force=False,
        )
    except SystemExit as exc:  # characters.add reports user errors this way
        message = str(exc.code) if exc.code not in (None, 0) else "Could not add the character."
        print(f"error: {message}", flush=True)
        return finish(False, message)
    except Exception as exc:  # noqa: BLE001 - surface anything, the panel shows it
        traceback.print_exc()
        return finish(False, _friendly(exc))
    print(f"Added {character.name!r}.", flush=True)
    return finish(True, "", character.slug)


def _friendly(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if "Connection refused" in text or "Max retries" in text or "Couldn't reach Voicebox" in text:
        return "Voicebox isn't running. Open the Voicebox app, then try again."
    return text[:400]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sample = sub.add_parser("sample")
    p_sample.add_argument("--slug", required=True)
    p_sample.add_argument("--out", required=True)
    p_sample.add_argument("--text", default=SAMPLE_TEXT)
    p_add = sub.add_parser("add")
    p_add.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    return sample(args) if args.cmd == "sample" else add(args)


if __name__ == "__main__":
    sys.exit(main())
