"""Scraper failure modes (no network, changed page) and the notifier's
webhook formats, all against local stubs."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from newsletter_video import notify, scraper


def test_no_network_raises_after_retries(monkeypatch):
    calls = []

    def dead(url, **kw):
        calls.append(url)
        raise requests.exceptions.ConnectionError("DNS down")

    monkeypatch.setattr(scraper.requests, "get", dead)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    with pytest.raises(requests.exceptions.ConnectionError):
        scraper.fetch_editions(["tech"])
    assert len(calls) == scraper.FETCH_ATTEMPTS


def test_changed_page_structure_yields_no_stories(monkeypatch):
    monkeypatch.setattr(scraper.requests, "get", lambda url, **kw: type("R", (), {"status_code": 200,
                        "text": "<html><body><h1>TLDR</h1><div>new layout, no articles</div></body></html>"})())
    assert scraper.fetch_editions(["tech"]) == []  # run_pipeline then aborts with "No stories scraped"


def test_404_days_are_skipped(monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append(url)
        return type("R", (), {"status_code": 404, "text": ""})()

    monkeypatch.setattr(scraper.requests, "get", get)
    assert scraper.fetch_editions(["ai"]) == []
    assert len(seen) == scraper.MAX_LOOKBACK_DAYS


@pytest.fixture
def webhook():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append({"path": self.path, "headers": dict(self.headers), "body": body})
            self.send_response(200)
            self.end_headers()

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", received
    httpd.shutdown()


def test_webhook_plain_text_for_ntfy(webhook, monkeypatch):
    url, received = webhook
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", url + "/my-topic")
    notify.notify("Pipeline failed", "boom", ok=False)
    assert received[0]["body"] == b"boom" and received[0]["headers"]["Title"] == "Pipeline failed"
    assert received[0]["headers"]["Priority"] == "high"


def test_webhook_json_when_forced(webhook, monkeypatch):
    url, received = webhook
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", url)
    monkeypatch.setenv("NOTIFY_WEBHOOK_FORMAT", "json")
    notify.notify("Pipeline finished", "3 video(s) made", ok=True)
    payload = json.loads(received[0]["body"])
    assert payload["text"].startswith("Pipeline finished") and payload["content"] == payload["text"]
    assert payload["status"] == "ok"


def test_webhook_failure_never_raises(monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "http://127.0.0.1:9/nothing-listens")
    notify.notify("x", "y")  # just must not raise


def test_discord_host_picks_json():
    assert notify._webhook_format("https://discord.com/api/webhooks/1/abc") == "json"
    assert notify._webhook_format("https://ntfy.sh/topic") == "text"
