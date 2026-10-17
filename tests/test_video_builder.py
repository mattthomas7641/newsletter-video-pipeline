"""Per-video isolation, immediate sidecars, collision-proof numbering and
render deadlines — with TTS, alignment and the renderer stubbed."""
from __future__ import annotations

import json
import subprocess
from datetime import date

import pytest

from newsletter_video import video_builder
from newsletter_video.runstate import load_state, next_index
from newsletter_video.supervisor import FatalPipelineError
from newsletter_video.youtube_upload import write_metadata
from tests.conftest import make_script, make_wav

DAY = date(2026, 10, 14)


@pytest.fixture
def stubbed_builder(monkeypatch, tmp_path):
    """TTS writes a tone, timing is estimated, the renderer is a stub that
    can be told to fail or hang per video title."""
    behaviour: dict = {"fail": set(), "hang": set(), "calls": []}

    def fake_synth(text, out_path, speed=None):
        return make_wav(out_path, seconds=20)

    monkeypatch.setattr(video_builder, "synthesize", fake_synth)
    monkeypatch.setattr(video_builder, "_align_script",
                        lambda lines, audio: video_builder._proportional_word_times(lines, 0.0, 19.0))
    monkeypatch.setattr(video_builder, "_cut_background", lambda clips, dst, duration, seed: False)
    monkeypatch.setattr(video_builder, "_pad_audio", lambda src, dst, seconds: dst.write_bytes(b"pad"))
    monkeypatch.setattr(video_builder, "_clean_render_cache", lambda work_dir: behaviour["calls"].append("clean"))
    monkeypatch.setattr(video_builder, "npx_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(video_builder, "hyperframes_env", lambda npx: {})
    monkeypatch.setattr(video_builder, "RENDER_ATTEMPTS", 1)

    def fake_render(cmd, timeout, **kw):
        out = cmd[cmd.index("--output") + 1]
        story = json.loads(json.loads(open(cmd[cmd.index("--variables-file") + 1]).read())["story"])
        assert story["cards"] and story["captions"]  # the composition variables were produced
        behaviour["calls"].append(out)
        if any(marker in out for marker in behaviour["hang"]):
            raise subprocess.TimeoutExpired(cmd, timeout)
        if any(marker in out for marker in behaviour["fail"]):
            raise subprocess.CalledProcessError(1, cmd)
        open(out, "wb").write(b"\x00" * 1024)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(video_builder, "run_subprocess", fake_render)
    return behaviour


def _build(tmp_path, scripts, on_rendered=None):
    out_dir = tmp_path / "output" / DAY.isoformat()
    return out_dir, video_builder.build_shorts(scripts, out_dir, tmp_path / "output" / "_audio" / DAY.isoformat(),
                                               DAY, on_rendered=on_rendered)


def test_failed_video_does_not_stop_the_day(stubbed_builder, tmp_path):
    stubbed_builder["fail"] = {"02_"}
    scripts = [make_script("Alpha story"), make_script("Beta story"), make_script("Gamma story")]
    sidecars = []
    out_dir, paths = _build(tmp_path, scripts, on_rendered=lambda s, p: sidecars.append(write_metadata(s, p)))
    assert [p.name for p in paths] == ["01_alpha-story.mp4", "03_gamma-story.mp4"]
    assert len(sidecars) == 2 and all(p.exists() for p in sidecars)
    state = load_state(out_dir)["videos"]
    assert state["02_beta-story"]["status"] == "failed" and "CalledProcessError" in state["02_beta-story"]["error"]
    assert state["01_alpha-story"]["status"] == "rendered"
    assert "clean" in stubbed_builder["calls"]  # cache cleaned even with a failure
    assert not (out_dir / ".partial").glob("*.mp4") or not list((out_dir / ".partial").glob("*.mp4"))


def test_sidecar_written_immediately_after_each_render(stubbed_builder, tmp_path):
    """Before: sidecars were written only after ALL renders, so a crash at
    video 3 orphaned videos 1-2. Now the callback runs per video."""
    seen = []

    def on_rendered(script, path):
        seen.append(sorted(p.name for p in path.parent.glob("*.mp4")))
        write_metadata(script, path)

    out_dir, paths = _build(tmp_path, [make_script("One"), make_script("Two")], on_rendered)
    assert seen[0] == ["01_one.mp4"]  # called when only the first video existed
    assert (out_dir / "01_one.json").exists() and (out_dir / "02_two.json").exists()


def test_all_failed_raises(stubbed_builder, tmp_path):
    stubbed_builder["fail"] = {"01_", "02_"}
    with pytest.raises(RuntimeError, match="Every video failed"):
        _build(tmp_path, [make_script("One"), make_script("Two")])


def test_render_timeout_is_a_per_video_failure(stubbed_builder, tmp_path):
    stubbed_builder["hang"] = {"01_"}
    out_dir, paths = _build(tmp_path, [make_script("Hangs"), make_script("Fine")])
    assert [p.name for p in paths] == ["02_fine.mp4"]
    assert "TimeoutExpired" in load_state(out_dir)["videos"]["01_hangs"]["error"]


def test_fatal_tts_error_stops_the_day_immediately(stubbed_builder, tmp_path, monkeypatch):
    def dead_tts(text, out_path, speed=None):
        raise FatalPipelineError("Voicebox app could not be launched")

    monkeypatch.setattr(video_builder, "synthesize", dead_tts)
    with pytest.raises(FatalPipelineError):
        _build(tmp_path, [make_script("One"), make_script("Two"), make_script("Three")])
    state = load_state(tmp_path / "output" / DAY.isoformat())["videos"]
    assert list(state) == ["01_one"]  # did not go on to waste time on 2 and 3


def test_numbering_skips_past_failed_and_partial_files(stubbed_builder, tmp_path):
    out_dir = tmp_path / "output" / DAY.isoformat()
    out_dir.mkdir(parents=True)
    (out_dir / "01_old.mp4").write_bytes(b"x")
    (out_dir / "01_old.json").write_text("{}")
    (out_dir / "03_failed-earlier.json").write_text("{}")  # a sidecar whose mp4 never appeared
    assert next_index(out_dir) == 4
    _, paths = _build(tmp_path, [make_script("New one")])
    assert paths[0].name == "04_new-one.mp4"  # before: len(*.mp4)+1 == 02, colliding with a re-run's numbering


def test_silent_tts_output_fails_the_video(stubbed_builder, tmp_path, monkeypatch):
    from newsletter_video import tts

    def silent(text, out_path, speed=None):
        make_wav(out_path, seconds=20, silent=True)
        tts.check_audio(out_path, text)  # what the real synthesize() does last
        return out_path

    monkeypatch.setattr(video_builder, "synthesize", silent)
    with pytest.raises(RuntimeError, match="Every video failed"):  # every clip is silent here
        _build(tmp_path, [make_script("Mute"), make_script("Also mute")])
    state = load_state(tmp_path / "output" / DAY.isoformat())["videos"]
    assert len(state) == 2 and all("silent" in v["error"] for v in state.values())
