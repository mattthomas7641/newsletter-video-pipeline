import io
import json

import pytest
from PIL import Image

from newsletter_video.ui import security
from tests.ui.conftest import multipart


# --- request guards, end to end through the real server ---

def test_index_is_served_with_token_cookie_and_hardening_headers(client):
    status, headers, body = client.request("GET", "/", token=False)
    assert status == 200
    assert client.token.encode() in body
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("host", ["evil.example", "evil.example:80", "127.0.0.1", "127.0.0.1:1", "localhost",
                                  "127.0.0.1.evil.example"])
def test_wrong_host_is_rejected_even_for_the_page(client, host):
    status, _, _ = client.request("GET", "/", host=host, token=False)
    assert status == 403
    status, _, _ = client.request("GET", "/api/settings", host=host)
    assert status == 403


def test_localhost_name_is_accepted_for_the_right_port(client):
    status, _, _ = client.request("GET", "/api/settings", host=f"localhost:{client.port}")
    assert status == 200


def test_api_reads_need_the_token_or_cookie(client):
    assert client.request("GET", "/api/settings", token=False)[0] == 403
    assert client.request("GET", "/api/settings", headers={"X-UI-Token": "wrong"}, token=False)[0] == 403
    assert client.request("GET", "/api/settings", headers={"Cookie": f"bytesize_ui={client.token}"},
                          token=False)[0] == 200


def test_cookie_alone_is_not_enough_to_change_things(client, paths):
    before = paths.env_path.read_text()
    status, _, _ = client.request("PUT", "/api/settings", json_body={"updates": {"VIDEOS_PER_DAY": 9}},
                                  token=False, headers={"Cookie": f"bytesize_ui={client.token}"})
    assert status == 403
    assert paths.env_path.read_text() == before


def test_state_changing_requests_reject_foreign_origins(client, paths):
    before = paths.env_path.read_text()
    for origin in ("http://evil.example", "https://127.0.0.1", "null", f"http://127.0.0.1.evil.test:{client.port}"):
        status, _, _ = client.request("PUT", "/api/settings", json_body={"updates": {"VIDEOS_PER_DAY": 9}},
                                      headers={"Origin": origin})
        assert status == 403, origin
    assert paths.env_path.read_text() == before


def test_same_origin_is_accepted(client):
    status, _, _ = client.request("PUT", "/api/settings", json_body={"updates": {"VIDEOS_PER_DAY": 4}},
                                  headers={"Origin": f"http://127.0.0.1:{client.port}",
                                           "Sec-Fetch-Site": "same-origin"})
    assert status == 200


def test_cross_site_fetch_metadata_is_rejected(client):
    status, _, _ = client.request("PUT", "/api/settings", json_body={"updates": {"VIDEOS_PER_DAY": 4}},
                                  headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_simple_form_posts_are_refused(client, paths):
    before = paths.env_path.read_text()
    status, _, _ = client.request("POST", "/api/runs", body=b"kind=batch&confirm=true",
                                  content_type="application/x-www-form-urlencoded")
    assert status == 415
    status, _, _ = client.request("POST", "/api/runs", body=b'{"kind":"batch","confirm":true}',
                                  content_type="text/plain", token=False)
    assert status in (403, 415)
    assert paths.env_path.read_text() == before


def test_unknown_methods_and_paths(client):
    assert client.request("DELETE", "/api/settings")[0] == 405
    assert client.request("GET", "/api/nope")[0] == 404
    assert client.request("GET", "/static/../server.py")[0] == 404
    assert client.request("GET", "/static/%2e%2e/server.py")[0] == 404
    assert client.request("GET", "/static/index.html")[0] == 404  # the token page is only served at /


def test_guard_unit_constant_time_compare_handles_odd_input():
    guard = security.Guard(port=9, token="abc")
    headers = {"Host": "127.0.0.1:9", "X-UI-Token": "abcd"}
    assert guard.check("GET", headers, api=True)[0] == 403
    headers["X-UI-Token"] = "abc"
    assert guard.check("GET", headers, api=True) is None


# --- names ---

def test_slug_and_name_cleaning():
    assert security.is_slug("spongebob") and security.is_slug("a-1")
    for bad in ("", "../x", "A", "a/b", "a b", "-a", "a" * 70):
        assert not security.is_slug(bad)
    assert security.clean_display_name("  <b>Sponge\nBob</b>\t") == "b Sponge Bob /b"
    assert len(security.clean_display_name("x" * 200)) == 40
    assert security.clean_persona("a\x00b\x1fc") == "a b c"


# --- upload validation ---

def png(size=(20, 30), alpha=True) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGBA" if alpha else "RGB", size, (10, 20, 30, 0) if alpha else (10, 20, 30))
    img.save(buf, "PNG")
    return buf.getvalue()


def test_image_validation_detects_type_by_content_and_transparency():
    assert security.validate_image(png())["has_transparency"] is True
    assert security.validate_image(png(alpha=False))["has_transparency"] is False
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "JPEG")
    assert security.validate_image(buf.getvalue())["kind"] == "jpeg"


@pytest.mark.parametrize("data,label", [
    (b"", "empty"),
    (b"GIF89a" + b"\x00" * 50, "gif"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "svg"),
    (b"MZ\x90\x00" + b"\x00" * 100, "exe"),
    (b"\x89PNG\r\n\x1a\n" + b"garbage", "truncated png"),
    (b"#!/bin/sh\nrm -rf /\n", "script"),
])
def test_image_validation_rejects_non_images(data, label):
    with pytest.raises(security.UploadError):
        security.validate_image(data)


def test_image_size_caps():
    with pytest.raises(security.UploadError):
        security.validate_image(png() + b"\x00" * security.MAX_IMAGE_BYTES)


def test_audio_validation_by_content():
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 30
    assert security.validate_audio(wav) == "wav"
    assert security.validate_audio(b"ID3\x04" + b"\x00" * 30) == "mp3"
    assert security.validate_audio(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 20) == "m4a"
    for bad in (b"", b"hello world, not audio", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20):
        with pytest.raises(security.UploadError):
            security.validate_audio(bad)
    with pytest.raises(security.UploadError):
        security.validate_audio(wav + b"\x00" * security.MAX_AUDIO_BYTES)


def test_multipart_parsing_keeps_filenames_out_of_paths():
    body, ctype = multipart({"name": "Gizmo"}, [("avatar", "../../etc/passwd.png", b"\x89PNGdata"),
                                                  ("voice", "clip one.wav", b"RIFFxxxxWAVE")])
    parts = security.parse_multipart(ctype, body)
    assert [p.name for p in parts] == ["name", "avatar", "voice"]
    assert parts[0].filename is None and parts[0].data == b"Gizmo"
    assert parts[1].data == b"\x89PNGdata"


def test_multipart_rejects_other_content_types():
    with pytest.raises(security.UploadError):
        security.parse_multipart("application/json", b"{}")


# --- through the API ---

def test_oversized_body_is_refused_before_reading(client):
    big = b"x" * (security.MAX_BODY_BYTES + 10)
    try:
        status, _, _ = client.request("POST", "/api/characters", body=big, content_type="multipart/form-data; boundary=x")
    except (ConnectionError, BrokenPipeError):
        return  # the server closed the connection early, which is also a refusal
    assert status == 413


def test_oversized_json_body_is_refused(client):
    status, body = client.send_json("PUT", "/api/settings", {"updates": {"VIDEOS_PER_DAY": "1" * 70000}})
    assert status == 400
