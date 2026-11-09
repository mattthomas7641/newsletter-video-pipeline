"""Each avatar keeps its own voice: a built-in Kokoro voice, or a cloned Voicebox one."""
from __future__ import annotations

import json

from newsletter_video import characters, tts


def _make(tmp_path, monkeypatch, slug, manifest):
    folder = tmp_path / slug
    folder.mkdir()
    (folder / "avatar.png").write_bytes(b"x")
    (folder / "character.json").write_text(json.dumps({"avatar": "avatar.png", **manifest}))
    monkeypatch.setattr(characters, "CHARACTERS_DIR", tmp_path)


def test_a_character_can_carry_a_kokoro_voice(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch, "puck", {"name": "Puck", "tts": "kokoro", "kokoro_voice": "am_puck"})
    c = characters.load("puck")
    assert (c.tts, c.kokoro_voice) == ("kokoro", "am_puck")


def test_a_kokoro_character_speaks_with_its_voice_whatever_the_global_provider(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch, "puck", {"name": "Puck", "tts": "kokoro", "kokoro_voice": "am_puck"})
    monkeypatch.setenv("CHARACTER", "puck")
    monkeypatch.setenv("TTS_PROVIDER", "voicebox")
    calls = []
    monkeypatch.setattr(tts, "_kokoro_backend", lambda text, out, speed=None, voice=None: calls.append(voice) or out)
    monkeypatch.setattr(tts, "_voicebox_backend", lambda *a, **k: calls.append("voicebox"))
    monkeypatch.setattr(tts, "check_audio", lambda *a, **k: None)
    tts.synthesize("hello there", tmp_path / "out.wav")
    assert calls == ["am_puck"]


def test_a_cloned_character_still_uses_voicebox(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch, "peter", {"name": "Peter", "voicebox_profile_id": "abc"})
    monkeypatch.setenv("CHARACTER", "peter")
    monkeypatch.setenv("TTS_PROVIDER", "kokoro")
    calls = []
    monkeypatch.setattr(tts, "_voicebox_backend", lambda text, out, speed=None: calls.append("voicebox") or out)
    monkeypatch.setattr(tts, "_kokoro_backend", lambda *a, **k: calls.append("kokoro"))
    monkeypatch.setattr(tts, "check_audio", lambda *a, **k: None)
    tts.synthesize("hello there", tmp_path / "out.wav")
    assert calls == ["voicebox"]


# ---- poses: the avatar swaps between several cutouts of the same character -------------------

def test_a_character_lists_its_extra_poses(tmp_path, monkeypatch):
    folder = tmp_path / "puck"
    folder.mkdir()
    for name in ("avatar.png", "pose-2.png", "pose-3.png"):
        (folder / name).write_bytes(b"x")
    (folder / "character.json").write_text(json.dumps({"name": "Puck", "avatar": "avatar.png", "poses": ["pose-2.png", "pose-3.png"]}))
    monkeypatch.setattr(characters, "CHARACTERS_DIR", tmp_path)
    c = characters.load("puck")
    assert [p.name for p in c.poses] == ["pose-2.png", "pose-3.png"]


def test_pose_changes_never_repeat_start_and_end_on_the_main_pose_and_are_repeatable():
    from newsletter_video.video_builder import pose_changes

    starts = [3.0, 9.0, 15.0, 21.0, 28.0]
    changes = pose_changes(4, starts, "seed-a")
    assert changes == pose_changes(4, starts, "seed-a")  # same video, same poses
    poses = [0] + [c["pose"] for c in changes]
    assert all(a != b for a, b in zip(poses, poses[1:]))  # never the same pose twice in a row
    assert poses[-1] == 0  # the outro is back on the main pose
    assert [c["at"] for c in changes] == starts


def test_one_pose_means_no_changes_and_quick_beats_are_not_flicked():
    from newsletter_video.video_builder import pose_changes

    assert pose_changes(1, [3.0, 9.0], "x") == []
    quick = pose_changes(3, [3.0, 3.5, 9.0], "x")
    assert [c["at"] for c in quick] == [3.0, 9.0]
