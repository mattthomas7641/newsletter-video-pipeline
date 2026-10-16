"""YOUTUBE_SCHEDULE publish slots: on time when the slot is ahead, soon (not
tomorrow) when it has already passed."""
from datetime import date, datetime, timedelta

from newsletter_video import youtube_upload


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_future_slot_is_kept(monkeypatch):
    now = datetime.now().astimezone()
    slot = (now + timedelta(hours=3)).replace(second=0, microsecond=0)
    monkeypatch.setenv("YOUTUBE_SCHEDULE", f"{slot:%H:%M}")
    (at,) = youtube_upload._publish_times(slot.date(), 1)
    assert _parse(at) == slot


def test_passed_slot_publishes_within_minutes_not_tomorrow(monkeypatch):
    now = datetime.now().astimezone()
    slot = (now - timedelta(hours=1)).replace(second=0, microsecond=0)
    monkeypatch.setenv("YOUTUBE_SCHEDULE", f"{slot:%H:%M}")
    (at,) = youtube_upload._publish_times(slot.date(), 1)
    delta = _parse(at) - now
    assert timedelta(minutes=4) <= delta <= timedelta(minutes=6)


def test_older_day_leftover_does_not_get_a_stale_schedule(monkeypatch):
    monkeypatch.setenv("YOUTUBE_SCHEDULE", "10:00")
    (at,) = youtube_upload._publish_times(date.today() - timedelta(days=3), 1)
    assert _parse(at) - datetime.now().astimezone() < timedelta(minutes=6)


def test_videos_beyond_the_slots_get_no_schedule(monkeypatch):
    monkeypatch.setenv("YOUTUBE_SCHEDULE", "10:00,10:00")
    times = youtube_upload._publish_times(date.today(), 4)
    assert times[2] is None and times[3] is None


def test_blank_schedule_means_none(monkeypatch):
    monkeypatch.setenv("YOUTUBE_SCHEDULE", "")
    assert youtube_upload._publish_times(date.today(), 3) == [None, None, None]
