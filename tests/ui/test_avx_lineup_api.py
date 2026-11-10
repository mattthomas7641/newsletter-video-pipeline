"""The panel's lineup API with avatar explainer (avx) batches."""
from tests.ui.conftest import make_character


def test_the_lineup_api_saves_and_expands_avx_batches(client, paths):
    make_character(paths, slug="puck", name="Puck")
    lineup = {"order": "sequential", "batches": [{"count": 2, "style": "avx", "character": "puck", "bg": "", "accent": ""},
                                                 {"count": 1, "style": "explainer", "preset": "paper"}]}
    status, data = client.send_json("POST", "/api/lineup/expand", {"lineup": lineup})
    assert status == 200 and data["ok"] and [s["family"] for s in data["slots"]] == ["avx", "avx", "paper"]
    assert data["slots"][0]["look"] == {"style": "avx", "bg": "", "accent": "", "character": "puck"}
    status, data = client.send_json("PUT", "/api/lineup", {"lineup": lineup})
    assert status == 200 and data["saved"] and data["lineup"]["batches"][0]["style"] == "avx"
    import os
    from pathlib import Path

    assert Path(os.environ["BYTESIZE_LINEUP_FILE"]).is_file()  # the test's own tmp file, never the real lineup.json
    status, data = client.send_json("PUT", "/api/lineup", {"lineup": {"batches": [{"count": 1, "style": "avx", "character": "ghost"}]}})
    assert status == 400 and "doesn't exist" in data["error"]
