"""A dropped or stale connection to Claude (e.g. after the Mac slept) must be
retried, not fail the whole daily run."""
import types

import pytest

from newsletter_video import script_writer as sw


class RemoteProtocolError(Exception):
    """Stands in for httpx's mid-stream error, which the SDK doesn't wrap."""
    __module__ = "httpx2.fake"


class APIError(Exception):
    pass


def _anthropic():
    return types.SimpleNamespace(APIError=APIError)


def _client(outcomes):
    calls = {"n": 0}

    class Stream:
        def __enter__(self):
            calls["n"] += 1
            outcome = outcomes[min(calls["n"], len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            self.result = outcome
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self.result

    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: Stream())))
    return client, calls


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_retries_a_dropped_connection_then_succeeds():
    client, calls = _client([RemoteProtocolError("incomplete chunked read"), "the answer"])
    assert sw._stream_with_retries(client, _anthropic()) == "the answer"
    assert calls["n"] == 2


def test_gives_up_after_every_retry_with_a_network_error():
    client, calls = _client([RemoteProtocolError("down")])
    with pytest.raises(sw.WriterNetworkError):
        sw._stream_with_retries(client, _anthropic())
    assert calls["n"] == 1 + len(sw.RETRY_DELAYS_SECONDS)


def test_a_real_api_error_is_not_retried():
    client, calls = _client([APIError("bad request")])
    with pytest.raises(APIError):
        sw._stream_with_retries(client, _anthropic())
    assert calls["n"] == 1


def test_os_errors_are_retried():
    client, calls = _client([ConnectionResetError("reset"), "ok"])
    assert sw._stream_with_retries(client, _anthropic()) == "ok"
