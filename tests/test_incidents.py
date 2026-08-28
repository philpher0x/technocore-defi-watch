"""Record validation, the stable id, and the one line that reaches the room."""

from __future__ import annotations

from datetime import date

import pytest

from defiwatch.incidents import (
    MAX_LINE_CHARS,
    ID_PATTERN,
    Incident,
    format_loss,
    ids_in,
    render,
)

PAYLOAD = {
    "protocol": "Example Finance",
    "chain": "Ethereum",
    "occurred_on": "2026-08-27",
    "loss_usd": 3_200_000,
    "vector": "price oracle manipulation",
    "summary": "An attacker moved the reported price of a thinly traded pool and drained the lending market against it.",
    "source_url": "https://rekt.news/example-finance",
}


def test_id_is_stable_across_wording_of_the_same_event() -> None:
    """Two articles, two summaries, one incident — the id has to agree."""
    first = Incident.from_payload(PAYLOAD)
    second = Incident.from_payload(
        {**PAYLOAD, "protocol": "  example finance  ", "summary": "different words entirely",
         "loss_usd": 3_400_000, "source_url": "https://theblock.co/other"}
    )

    assert first.incident_id == second.incident_id


def test_id_separates_different_dates() -> None:
    other_day = Incident.from_payload({**PAYLOAD, "occurred_on": "2026-08-28"})

    assert Incident.from_payload(PAYLOAD).incident_id != other_day.incident_id


@pytest.mark.parametrize(
    "broken",
    [
        {"occurred_on": "last Tuesday"},
        {"occurred_on": ""},
        {"source_url": "rekt.news/no-scheme"},
        {"source_url": ""},
        {"protocol": "   "},
    ],
)
def test_unusable_records_are_rejected(broken: dict) -> None:
    with pytest.raises(ValueError):
        Incident.from_payload({**PAYLOAD, **broken})


def test_missing_loss_is_allowed_but_unparseable_loss_is_not_invented() -> None:
    assert Incident.from_payload({**PAYLOAD, "loss_usd": None}).loss_usd is None
    assert Incident.from_payload({**PAYLOAD, "loss_usd": "a lot"}).loss_usd is None
    assert Incident.from_payload({**PAYLOAD, "loss_usd": "2500"}).loss_usd == 2500.0


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, "loss undisclosed"),
        (0, "loss undisclosed"),
        (850, "loss ~$850"),
        (12_500, "loss ~$12.5K"),
        (3_200_000, "loss ~$3.2M"),
        (1_100_000_000, "loss ~$1.1B"),
    ],
)
def test_loss_is_rounded_to_the_precision_reporting_has(amount, expected) -> None:
    assert format_loss(amount) == expected


def test_rendered_line_is_single_line_and_carries_its_id() -> None:
    line = render(Incident.from_payload(PAYLOAD))

    assert "\n" not in line
    assert ID_PATTERN.search(line)
    assert "Example Finance (Ethereum)" in line
    assert "loss ~$3.2M" in line
    assert PAYLOAD["source_url"] in line


def test_long_summary_is_trimmed_to_fit() -> None:
    line = render(Incident.from_payload({**PAYLOAD, "summary": "detail. " * 500}))

    assert len(line) <= MAX_LINE_CHARS
    assert line.endswith(PAYLOAD["source_url"])


def test_ids_are_recovered_from_published_lines() -> None:
    incident = Incident.from_payload(PAYLOAD)
    published = [render(incident), "unrelated chatter", "id:notvalidhex!!"]

    assert ids_in(published) == {incident.incident_id}


def test_missing_chain_and_vector_get_honest_placeholders() -> None:
    sparse = Incident.from_payload({**PAYLOAD, "chain": "", "vector": ""})

    assert sparse.chain == "unspecified chain"
    assert sparse.vector == "vector unreported"
    assert sparse.occurred_on == date(2026, 8, 27)
