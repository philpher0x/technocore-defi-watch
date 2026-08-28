"""The incident record, its stable id, and the single line it becomes in the room.

The id is derived from the protocol, chain and date rather than assigned, so two
runs that read the same event off two different articles agree on what it is —
that is what makes deduplication work across restarts and across a lost state
note. It travels *inside* the published line (`id:<12 hex>`), which lets the room
itself serve as the deduplication source of truth when the note is gone.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

ID_LENGTH = 12
ID_PATTERN = re.compile(r"\bid:([0-9a-f]{%d})\b" % ID_LENGTH)
MAX_SUMMARY_CHARS = 320
# Well under the server's 4096 cap: a line nobody reads to the end is not useful,
# and the room's ring buffer is shared with every other line we will ever write.
MAX_LINE_CHARS = 900


@dataclass(frozen=True)
class Incident:
    protocol: str
    chain: str
    occurred_on: date
    loss_usd: float | None
    vector: str
    summary: str
    source_url: str

    @property
    def incident_id(self) -> str:
        """Stable across runs: same event, same id, whatever article described it."""
        seed = f"{self.protocol.strip().lower()}|{self.chain.strip().lower()}|{self.occurred_on:%Y-%m-%d}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:ID_LENGTH]

    @classmethod
    def from_payload(cls, payload: dict) -> "Incident":
        """Build from one model-produced object, rejecting anything unusable.

        The model's output is input like any other: a malformed date or a missing
        source means the record is dropped, never guessed at.
        """
        occurred_raw = str(payload.get("occurred_on", "")).strip()
        try:
            occurred = datetime.strptime(occurred_raw, "%Y-%m-%d").date()
        except ValueError as error:
            raise ValueError(f"unusable occurred_on {occurred_raw!r}") from error

        source = str(payload.get("source_url", "")).strip()
        if not source.startswith(("http://", "https://")):
            raise ValueError(f"unusable source_url {source!r}")

        protocol = str(payload.get("protocol", "")).strip()
        if not protocol:
            raise ValueError("incident has no protocol")

        loss = payload.get("loss_usd")
        try:
            loss_usd = float(loss) if loss is not None else None
        except (TypeError, ValueError):
            loss_usd = None

        return cls(
            protocol=protocol,
            chain=str(payload.get("chain", "")).strip() or "unspecified chain",
            occurred_on=occurred,
            loss_usd=loss_usd,
            vector=str(payload.get("vector", "")).strip() or "vector unreported",
            summary=str(payload.get("summary", "")).strip(),
            source_url=source,
        )


def format_loss(loss_usd: float | None) -> str:
    """Round money to the precision the reporting actually has."""
    if loss_usd is None or loss_usd <= 0:
        return "loss undisclosed"
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if loss_usd >= threshold:
            return f"loss ~${loss_usd / threshold:.1f}{suffix}"
    return f"loss ~${loss_usd:.0f}"


def render(incident: Incident) -> str:
    """One incident, one line — the only shape the room accepts."""
    summary = " ".join(incident.summary.split())
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"

    line = (
        f"DEFI-INCIDENT id:{incident.incident_id}"
        f" | {incident.occurred_on:%Y-%m-%d}"
        f" | {incident.protocol} ({incident.chain})"
        f" | {format_loss(incident.loss_usd)}"
        f" | {incident.vector}"
        f" | {summary}"
        f" | source: {incident.source_url}"
    )
    if len(line) > MAX_LINE_CHARS:
        overflow = len(line) - MAX_LINE_CHARS
        trimmed = summary[: max(0, len(summary) - overflow - 1)].rstrip() + "…"
        line = line.replace(f"| {summary} |", f"| {trimmed} |", 1)
    return line


def ids_in(texts: list[str]) -> set[str]:
    """Every incident id already visible in a batch of room lines."""
    found: set[str] = set()
    for text in texts:
        found.update(ID_PATTERN.findall(text or ""))
    return found
