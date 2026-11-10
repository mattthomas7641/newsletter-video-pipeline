"""Pose roles: adding a pose to a character, and the rule that the shrug is only for the last scene."""
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from newsletter_video import characters
from newsletter_video.characters import plan_poses

BG = (238, 221, 212)
ROLES5 = ["neutral", "point", "think", "shrug", "present"]


def _figure(path, arm=(0, 0), size=(300, 420)):
    """A flat-background drawing of a stick-ish figure; `arm` = where the right hand reaches (dx, dy)."""
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)
    d.ellipse((120, 40, 180, 100), fill=(60, 40, 30))  # head
    d.rectangle((115, 100, 185, 260), fill=(20, 30, 70))  # body
    d.rectangle((120, 260, 145, 390), fill=(60, 40, 25))  # legs
    d.rectangle((155, 260, 180, 390), fill=(60, 40, 25))
    d.line((185, 120, 185 + 60 + arm[0], 160 + arm[1]), fill=(20, 30, 70), width=18)  # right arm
    img.save(path)
    return path


@pytest.fixture
def puck_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(characters, "CHARACTERS_DIR", tmp_path)
    d = tmp_path / "puck"
    d.mkdir()
    (d / "character.json").write_text(json.dumps({"name": "Puck", "avatar": "avatar.png"}))
    _figure(d / "avatar.png")
    return tmp_path


def _alpha(path):
    return np.asarray(Image.open(path).getchannel("A")) > 128


def test_add_pose_with_the_stored_canvas_matches_cutting_all_poses_together(puck_dir, tmp_path):
    from newsletter_video.cutout import cut_poses

    a = _figure(tmp_path / "a.png")
    b = _figure(tmp_path / "b.png", arm=(10, -60))
    c = _figure(tmp_path / "c.png", arm=(70, 40))  # reaches outside the first two poses' box
    ch = characters.set_poses("puck", [a, b], ["neutral", "shrug"])
    manifest = json.loads((ch.directory / "character.json").read_text())
    assert manifest["pose_roles"] == ["neutral", "shrug"]
    assert manifest["canvas"]["source_size"] == [300, 420] and len(manifest["canvas"]["box"]) == 4
    ch = characters.add_pose("puck", c, "present")
    assert ch.pose_roles == ("neutral", "shrug", "present")
    together = cut_poses([a, b, c])
    pictures = ch.pictures()
    assert len(pictures) == 3
    for pic, ref in zip(pictures, together):
        assert Image.open(pic).size == ref.size  # every picture padded to the grown box
        got, want = _alpha(pic), np.asarray(ref.getchannel("A")) > 128
        assert (got != want).mean() < 0.002  # pixel-for-pixel the same placement


def test_add_pose_without_a_stored_canvas_lines_it_up_by_the_body(puck_dir, tmp_path):
    a = _figure(tmp_path / "a.png")
    characters.set_poses("puck", [a, _figure(tmp_path / "b.png", arm=(0, -40))])
    manifest_path = puck_dir / "puck" / "character.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("canvas")  # a character made before the canvas was stored
    manifest_path.write_text(json.dumps(manifest))
    ch = characters.add_pose("puck", _figure(tmp_path / "c.png", arm=(5, 20)), "present")
    main, new = _alpha(ch.pictures()[0]), _alpha(ch.pictures()[-1])
    assert main.shape == new.shape
    ys_m, xs_m = np.nonzero(main)
    ys_n, xs_n = np.nonzero(new)
    assert abs(ys_m.max() - ys_n.max()) <= 2  # same floor line
    legs = lambda ys, xs: xs[ys > ys.max() - 60].mean()
    assert abs(legs(ys_m, xs_m) - legs(ys_n, xs_n)) <= 2  # legs centred on the main pose's legs


def test_add_pose_refuses_a_role_the_character_already_has(puck_dir, tmp_path):
    characters.set_poses("puck", [_figure(tmp_path / "a.png"), _figure(tmp_path / "b.png", arm=(0, -40))],
                         ["neutral", "shrug"])
    with pytest.raises(SystemExit):
        characters.add_pose("puck", _figure(tmp_path / "c.png"), "shrug")


def test_a_manifest_without_roles_gets_defaults_with_no_shrug(puck_dir):
    ch = characters.load("puck")
    assert ch.pose_roles == ("neutral",)
    assert characters.pose_roles_for({"pose_roles": ["a", "b"]}, 3) == ["neutral", "pose2", "pose3"]


@pytest.mark.parametrize("seed", ["a", "b", "c", "d"])
def test_plan_poses_keeps_the_shrug_for_the_last_scene_and_never_repeats(seed):
    kinds = ["kinetic", "stat", "chart", "versus", "timeline", "list", "relation", "outro"]
    plan = plan_poses(ROLES5, len(kinds), wanted=["shrug", "shrug", 3, "point", "point", None, "think", "neutral"],
                      kinds=kinds, seed=seed)
    assert plan[-1] == ROLES5.index("shrug")
    assert ROLES5.index("shrug") not in plan[:-1]
    assert all(x != y for x, y in zip(plan, plan[1:]))
    assert plan == plan_poses(ROLES5, len(kinds), wanted=["shrug", "shrug", 3, "point", "point", None, "think", "neutral"],
                              kinds=kinds, seed=seed)


def test_plan_poses_fits_poses_to_scenes_and_uses_a_new_pose():
    plan = plan_poses(ROLES5, 6, kinds=["kinetic", "stat", "timeline", "versus", "list", "outro"], seed="x")
    roles = [ROLES5[k] for k in plan]
    assert roles[0] == "neutral" and roles[1] == "point" and roles[2] == "think" and roles[-1] == "shrug"
    assert "present" in roles  # the new pose joins the rotation
    # Without the new pose nothing breaks: the same rules with four poses
    plan4 = plan_poses(ROLES5[:4], 6, kinds=["kinetic", "stat", "timeline", "versus", "list", "outro"], seed="x")
    assert plan4[-1] == 3 and 3 not in plan4[:-1] and all(x != y for x, y in zip(plan4, plan4[1:]))


def test_avatar_format_pose_changes_use_the_shrug_only_for_the_outro():
    from newsletter_video.video_builder import pose_changes

    starts = [3.0, 9.0, 15.0, 21.0, 28.0, 33.0]
    for seed in ("a", "b", "c"):
        changes = pose_changes(5, starts, seed, roles=ROLES5)
        poses = [0] + [c["pose"] for c in changes]
        assert changes[-1] == {"at": 33.0, "pose": 3}
        assert 3 not in poses[:-1]
        assert all(x != y for x, y in zip(poses, poses[1:]))
    # the outro's shrug is applied even when it comes quickly after the last beat
    quick = pose_changes(4, [3.0, 9.0, 10.0], "x", roles=ROLES5[:4])
    assert quick[-1] == {"at": 10.0, "pose": 3}


def test_avx_avatar_assets_replace_an_early_shrug(monkeypatch, tmp_path):
    from newsletter_video import avx_builder as avx
    from newsletter_video import video_builder as vb

    pics = []
    for k in range(5):
        p = tmp_path / f"p{k}.png"
        Image.new("RGBA", (40, 80)).save(p)
        pics.append(p)
    monkeypatch.setattr(vb, "character_image", lambda: pics[0])
    monkeypatch.setattr(vb, "character_poses", lambda: pics[1:])
    monkeypatch.setattr(vb, "character_pose_roles", lambda: ROLES5)
    out = tmp_path / "assets"
    out.mkdir()
    av = avx._avatar_assets(out, "s", [0.0, 3.0, 6.0, 9.0, 12.0], ["shrug", 3, "point", "point", "think"],
                            ["kinetic", "stat", "chart", "versus", "outro"])
    plan = [av["first"]] + [c["pose"] for c in av["changes"]]
    assert plan[-1] == 3 and 3 not in plan[:-1] and av["changes"][-1]["at"] == 12.0
    assert all(x != y for x, y in zip(plan, plan[1:]))


def test_avx_writer_repairs_a_shrug_before_the_last_scene():
    from newsletter_video import avx_writer as w
    from tests.avx_fixtures import STORY, video

    v = video()
    v["scenes"][1]["pose"] = "shrug"
    v["scenes"][-1]["pose"] = "shrug"
    scenes, repairs = w.check_video(v, [STORY])
    assert "pose" not in scenes[1] and scenes[-1]["pose"] == "shrug"
    assert any("shrug is kept for the last scene" in r for r in repairs)


def test_the_writer_prompt_lists_the_characters_roles(monkeypatch):
    from newsletter_video import avx_writer as w

    monkeypatch.setattr(w, "pose_roles", lambda: ROLES5)
    text = w._poses_text()
    assert '"present"' in text and text.rstrip().endswith('ONLY for the last scene, the closing question')
    assert "@POSES@" not in w._system_prompt()
