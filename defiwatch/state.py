"""What the agent remembers between runs, kept on Technocore itself.

There is no database and no committed state file: the cursor lives in a note
under a private namespace, so the workflow needs no write access to the repo.
Notes are reclaimed after seven days of silence, which a schedule measured in
hours never reaches — but the note can still vanish, so the room is read as a
second source and any incident id already visible there counts as seen.

That redundancy is the whole reason the id travels inside the published line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .incidents import ids_in
from .technocore import Client, TechnocoreError

STATE_VERSION = 1
# A note caps at 8192 characters. 300 ids is roughly 4.5 KB of JSON, leaving room
# for the cursor and for the format to grow without a migration.
MAX_SEEN = 300
# After a long outage, do not drag in months of history on the first run back.
MAX_WINDOW_HOURS = 168.0


@dataclass
class State:
    last_run: datetime | None = None
    seen: list[str] = field(default_factory=list)
    degraded: bool = False

    @classmethod
    def load(cls, client: Client, namespace: str, key: str, room: str) -> "State":
        """Read the cursor note, then reinforce it with what the room already shows."""
        state = cls()
        try:
            raw = client.read_note(namespace, key)
        except TechnocoreError:
            raw = None
            state.degraded = True

        if raw:
            try:
                stored = json.loads(raw)
                stamp = stored.get("last_run")
                if stamp:
                    state.last_run = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                state.seen = [str(i) for i in stored.get("seen", [])]
            except (ValueError, AttributeError):
                # A corrupt note is not worth failing the run over; the room read
                # below still prevents duplicates.
                state.degraded = True
        elif raw is None and not state.degraded:
            # Never written, or reclaimed. Either way this is a cold start.
            state.degraded = True

        try:
            published = [str(m.get("text", "")) for m in client.read_room(room, limit=200)]
            state.seen = sorted(set(state.seen) | ids_in(published))
        except TechnocoreError:
            # Losing this only costs us the second opinion; the note may still hold.
            pass

        return state

    def window_hours(self, lookback_hours: float) -> float:
        """How far back to search: since the last run, floored and capped."""
        if self.last_run is None:
            return lookback_hours
        elapsed = (datetime.now(timezone.utc) - self.last_run).total_seconds() / 3600
        return min(max(elapsed, lookback_hours), MAX_WINDOW_HOURS)

    def earliest_date(self, lookback_hours: float):
        """The oldest incident date a run will accept, with a day of reporting lag.

        Exploits are frequently written up a day after they happen, so the date
        filter is deliberately looser than the search window. Duplicates are held
        off by the id, not by the date.
        """
        window = self.window_hours(lookback_hours)
        return (datetime.now(timezone.utc) - timedelta(hours=window + 24)).date()

    def is_new(self, incident_id: str) -> bool:
        return incident_id not in set(self.seen)

    def record(self, incident_ids: list[str]) -> None:
        self.seen = sorted(set(self.seen) | set(incident_ids))[-MAX_SEEN:]

    def save(self, client: Client, namespace: str, key: str) -> bool:
        """Persist the cursor. A failure here is logged, never fatal."""
        payload = json.dumps(
            {
                "version": STATE_VERSION,
                "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seen": self.seen[-MAX_SEEN:],
            },
            separators=(",", ":"),
        )
        try:
            return client.write_note(namespace, key, payload)
        except TechnocoreError:
            return False
