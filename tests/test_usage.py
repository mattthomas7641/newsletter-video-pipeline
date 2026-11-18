"""The per-run Claude budget (usage.py): counted from every call, capped, reported, and a spent budget fails the
next call in a way the writers already handle."""
from __future__ import annotations

import types

import pytest

from newsletter_video import script_writer as sw
from newsletter_video import usage


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("CLAUDE_RUN_BUDGET_USD", raising=False)
    usage.reset()
    yield
    usage.reset()


def u(i=0, o=0, cw=0, cr=0):
    return types.SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def test_sonnet_cost_is_two_and_ten_dollars_per_million():
    assert usage.cost_of("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert usage.cost_of("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(10.0)
    assert usage.cost_of("claude-sonnet-5-5", 0, 0, cache_write=1_000_000) == pytest.approx(2.5)
    assert usage.cost_of("claude-sonnet-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.1)


def test_an_unknown_model_is_costed_at_the_highest_known_rate():
    assert usage.cost_of("some-new-model", 1_000_000, 1_000_000) == pytest.approx(60.0)


def test_a_full_measured_writer_day_is_about_one_seventy():
    usage.record("claude-sonnet-5-5", u(122_439, 148_795))
    assert 1.6 < usage.spent() < 1.8
    assert "Claude about $1." in usage.summary() and "calls" in usage.summary()


def test_the_budget_stops_the_next_call_but_not_the_ones_before_it(monkeypatch):
    monkeypatch.setenv("CLAUDE_RUN_BUDGET_USD", "1.00")
    usage.check()
    usage.record("claude-sonnet-5-5", u(0, 100_000))  # $1.00
    with pytest.raises(usage.BudgetExceeded):
        usage.check()
    assert "limit" in usage.summary()


def test_a_zero_budget_means_no_limit(monkeypatch):
    monkeypatch.setenv("CLAUDE_RUN_BUDGET_USD", "0")
    usage.record("claude-sonnet-5-5", u(0, 10_000_000))
    usage.check()


def test_the_shared_claude_wrapper_records_usage_and_refuses_once_the_budget_is_spent(monkeypatch):
    monkeypatch.setenv("CLAUDE_RUN_BUDGET_USD", "0.50")

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return types.SimpleNamespace(usage=u(1000, 60_000), content=[])

    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: Stream())))
    fake_anthropic = types.SimpleNamespace(APIConnectionError=ConnectionError, APITimeoutError=TimeoutError)
    sw._stream_with_retries(client, fake_anthropic, model="claude-sonnet-5-5")  # $0.60: allowed, it started under budget
    assert usage.spent() == pytest.approx(0.602, abs=0.01)
    with pytest.raises(sw.WriterNetworkError, match="budget"):
        sw._stream_with_retries(client, fake_anthropic, model="claude-sonnet-5-5")
