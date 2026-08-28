"""The research half: search the live web, then turn the findings into records.

Deliberately two calls rather than one. The first runs `web_search` and is free
to write prose; the second sees only that prose and is pinned to a strict JSON
schema. Combining a browsing tool with a rigid output format tends to cost
either the browsing or the structure, and separating them also means a malformed
extraction can be retried without paying for the search again.

The model's answer is untrusted input. Nothing it returns reaches the room
without passing `Incident.from_payload`, and a record with an unusable date or a
missing source is dropped rather than repaired.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from openai import OpenAI

from .incidents import Incident

INCIDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "incidents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "Name of the protocol, bridge or platform that lost funds.",
                    },
                    "chain": {
                        "type": "string",
                        "description": "Chain or network it happened on, e.g. Ethereum, BNB Chain, Solana.",
                    },
                    "occurred_on": {
                        "type": "string",
                        "description": "Date the incident happened, strictly YYYY-MM-DD.",
                    },
                    "loss_usd": {
                        "type": ["number", "null"],
                        "description": "Reported loss in US dollars, or null when no figure was reported.",
                    },
                    "vector": {
                        "type": "string",
                        "description": "Attack vector in a few words, e.g. price oracle manipulation, private key compromise.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One sentence of concrete detail. No hedging, no advice, no promotion.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "Direct URL to the reporting this record came from.",
                    },
                },
                "required": [
                    "protocol",
                    "chain",
                    "occurred_on",
                    "loss_usd",
                    "vector",
                    "summary",
                    "source_url",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["incidents"],
    "additionalProperties": False,
}

SEARCH_PROMPT = """Today is {today}. Search the web for DeFi security incidents \
that became public in the last {window:.0f} hours.

Count only incidents where funds were actually lost or stolen from a protocol, \
bridge, exchange or smart contract: exploits, hacks, oracle manipulations, \
private key compromises, governance attacks, rug pulls with on-chain evidence.

Do not count: individual users phished, unconfirmed rumours, disclosed \
vulnerabilities that were patched without loss, token price movements, routine \
audits, marketing announcements, or retrospectives about older incidents.

For each incident report the protocol, the chain, the date it happened, the \
reported loss in US dollars, the attack vector, and a direct URL to the \
reporting. State plainly when there were none — that is a normal and frequent \
answer, and inventing an incident is worse than reporting nothing."""

EXTRACT_PROMPT = """Convert the report below into structured records.

Include only incidents that happened on or after {earliest}. Every record needs \
a real source URL taken from the report — never construct one. If the report \
describes no qualifying incident, return an empty list.

Report:
{report}"""


class ResearchError(RuntimeError):
    """The model could not be reached, or returned something unusable."""


class Researcher:
    def __init__(self, api_key: str, model: str, domains: list[str] | None = None) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._domains = domains or []

    def _search_tool(self) -> dict:
        tool: dict = {"type": "web_search"}
        if self._domains:
            # Up to 100 hosts, subdomains included, no scheme.
            tool["filters"] = {"allowed_domains": self._domains}
        return tool

    def search(self, window_hours: float) -> str:
        """Browse for incidents and return the model's prose report."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            response = self._client.responses.create(
                model=self._model,
                tools=[self._search_tool()],
                input=SEARCH_PROMPT.format(today=today, window=window_hours),
            )
        except Exception as error:  # the SDK raises a family of transport errors
            raise ResearchError(f"web search failed: {error}") from error
        return (response.output_text or "").strip()

    def extract(self, report: str, earliest) -> list[Incident]:
        """Turn the report into validated records, dropping anything malformed."""
        if not report:
            return []
        try:
            response = self._client.responses.create(
                model=self._model,
                input=EXTRACT_PROMPT.format(earliest=earliest, report=report),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "defi_incidents",
                        "schema": INCIDENT_SCHEMA,
                        "strict": True,
                    }
                },
            )
        except Exception as error:
            raise ResearchError(f"extraction failed: {error}") from error

        try:
            payload = json.loads(response.output_text or "{}")
        except ValueError as error:
            raise ResearchError("extraction did not return JSON") from error

        incidents = []
        for record in payload.get("incidents", []):
            try:
                incidents.append(Incident.from_payload(record))
            except ValueError:
                # One unusable record does not invalidate the rest of the batch.
                continue
        return incidents
