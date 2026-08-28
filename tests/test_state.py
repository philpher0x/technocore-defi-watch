"""The cursor: how far back a run looks, and what it refuses to say twice."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from defiwatch.state import MAX_SEEN, MAX_WINDOW_HOURS, State
from defiwatch.technocore import TechnocoreError


class FakeClient:
    """Stands in for the HTTP client: no network, and it records what was written."""

    def __init__(self, note: str | None = None, room_lines: list[str] | None = None,
                 note_raises: bool = False, room_raises: bool = False) -> None:
        self.note = note
        self.room_lines = room_lines or []
        self.note_raises = note_raises
        self.room_raises = room_raises
        self.written: list[str] = []

    def read_note(self, namespace: str, key: str) -> str | None:
        if self.note_raises:
            raise TechnocoreError("note unreachable")
        return self.note

    def read_room(self, room: str, *, limit: int = 50) -> list[dict]:
        if self.room_raises:
            raise TechnocoreError("room unreachable")
        return [{"text": line} for line in self.room_lines]

    def write_note(self, namespace: str, key: str, value: str) -> bool:
        self.written.append(value)
        return True


def _stored(last_run: datetime | None, seen: list[str]) -> str:
    return json.dumps(
        {"version": 1, "last_run": last_run.isoformat() if last_run else None, "seen": seen}
    )


def test_cold_start_uses_the_configured_lookback() -> None:
    state = State.load(FakeClient(), "p-ns", "key", "d-defi-watch")

    assert state.window_hours(24) == 24
    assert state.degraded is True


def test_window_never_shrinks_below_the_lookback() -> None:
    """Running every 30 minutes must not narrow the window to 30 minutes."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=30)
    state = State.load(FakeClient(note=_stored(recent, [])), "p-ns", "key", "d-defi-watch")

    assert state.window_hours(24) == 24


def test_window_widens_to_cover_a_missed_gap() -> None:
    stale = datetime.now(timezone.utc) - timedelta(hours=50)
    state = State.load(FakeClient(note=_stored(stale, [])), "p-ns", "key", "d-defi-watch")

    assert 49 < state.window_hours(24) < 51


def test_window_is_capped_after_a_long_outage() -> None:
    ancient = datetime.now(timezone.utc) - timedelta(days=90)
    state = State.load(FakeClient(note=_stored(ancient, [])), "p-ns", "key", "d-defi-watch")

    assert state.window_hours(24) == MAX_WINDOW_HOURS


def test_ids_published_in_the_room_survive_a_lost_note() -> None:
    """The note is gone, but the room still shows what was said — no reposting."""
    client = FakeClient(note=None, room_lines=["DEFI-INCIDENT id:abc123def456 | ... "])
    state = State.load(client, "p-ns", "key", "d-defi-watch")

    assert state.is_new("abc123def456") is False
    assert state.is_new("000000000000") is True


def test_corrupt_note_degrades_instead_of_failing() -> None:
    client = FakeClient(note="{not json", room_lines=["DEFI-INCIDENT id:abc123def456 |"])
    state = State.load(client, "p-ns", "key", "d-defi-watch")

    assert state.degraded is True
    assert state.is_new("abc123def456") is False


def test_unreachable_note_still_reads_the_room() -> None:
    client = FakeClient(note_raises=True, room_lines=["DEFI-INCIDENT id:abc123def456 |"])
    state = State.load(client, "p-ns", "key", "d-defi-watch")

    assert state.degraded is True
    assert state.is_new("abc123def456") is False


def test_unreachable_room_falls_back_to_the_note() -> None:
    stored = _stored(datetime.now(timezone.utc), ["abc123def456"])
    client = FakeClient(note=stored, room_raises=True)
    state = State.load(client, "p-ns", "key", "d-defi-watch")

    assert state.is_new("abc123def456") is False


def test_seen_list_is_trimmed_to_the_note_budget() -> None:
    state = State()
    state.record([f"{n:012x}" for n in range(MAX_SEEN + 50)])

    assert len(state.seen) == MAX_SEEN


def test_save_writes_a_cursor_the_next_run_can_read() -> None:
    client = FakeClient()
    state = State(seen=["abc123def456"])

    assert state.save(client, "p-ns", "key") is True
    stored = json.loads(client.written[0])
    assert stored["seen"] == ["abc123def456"]
    assert datetime.fromisoformat(stored["last_run"])


def test_earliest_date_allows_for_reporting_lag() -> None:
    """Write-ups lag the exploit, so the date filter is looser than the window."""
    state = State()
    earliest = state.earliest_date(24)

    assert earliest == (datetime.now(timezone.utc) - timedelta(hours=48)).date()
