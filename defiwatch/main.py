"""One pass of the watch: observe, decide, and usually say nothing.

The agent is obliged to *check* on a schedule. It is not obliged to publish, and
most runs end with nothing posted — a quiet week is a correct week. Everything
that could turn this into another presence-ping bot is deliberately absent:
there is no fallback message, no "no incidents today" line, no greeting.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from .config import Config, ConfigError
from .identity import Identity, IdentityError
from .incidents import Incident, render
from .research import ResearchError, Researcher
from .state import State
from .technocore import Client, TechnocoreError

# Space consecutive posts: the room refuses copies of one text inside a short
# window, and the write bucket refills continuously rather than in bursts.
SECONDS_BETWEEN_POSTS = 4

log = logging.getLogger("defiwatch")


def _load_fixture(path: str) -> list[Incident]:
    """Debug path: replay a saved batch instead of paying for a search."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("incidents", payload) if isinstance(payload, dict) else payload
    incidents = []
    for record in records:
        try:
            incidents.append(Incident.from_payload(record))
        except ValueError as error:
            log.warning("fixture record dropped: %s", error)
    return incidents


def _worth_publishing(incident: Incident, config: Config, earliest) -> bool:
    """Filter on age and size only — relevance was the model's job."""
    if incident.occurred_on < earliest:
        log.info("skip %s: dated %s, before %s", incident.protocol, incident.occurred_on, earliest)
        return False
    # An unreported loss is common in the first hours of a real incident, so a
    # missing figure is not disqualifying; a figure below the floor is.
    if incident.loss_usd is not None and incident.loss_usd < config.min_loss_usd:
        log.info(
            "skip %s: reported loss %.0f below floor %.0f",
            incident.protocol, incident.loss_usd, config.min_loss_usd,
        )
        return False
    return True


def run() -> int:
    config = Config.from_env()
    identity = Identity.from_pem(config.identity_pem, config.identity_passphrase or None)
    if config.expected_did and identity.did != config.expected_did:
        raise IdentityError(
            f"loaded key is {identity.did}, but TECHNOCORE_DID says {config.expected_did}"
        )
    log.info("signing as %s, target room /r/%s", identity.did, config.room)

    client = Client(config.base_url)
    state = State.load(client, config.state_namespace, config.state_key, config.room)
    window = state.window_hours(config.lookback_hours)
    earliest = state.earliest_date(config.lookback_hours)
    log.info(
        "window %.1fh, accepting incidents on or after %s, %d ids already seen%s",
        window, earliest, len(state.seen), " (state degraded)" if state.degraded else "",
    )

    fixture = os.environ.get("DEFIWATCH_FIXTURE", "").strip()
    if fixture:
        log.warning("using fixture %s — no web search performed", fixture)
        found = _load_fixture(fixture)
    else:
        researcher = Researcher(config.openai_api_key, config.openai_model, config.search_domains)
        report = researcher.search(window)
        log.info("search returned %d characters of report", len(report))
        found = researcher.extract(report, earliest)

    log.info("model produced %d candidate incidents", len(found))

    fresh = [i for i in found if _worth_publishing(i, config, earliest) and state.is_new(i.incident_id)]
    # Newest first, then largest — if the cap bites, it should drop the smallest
    # and oldest rather than whatever the model happened to list last.
    fresh.sort(key=lambda i: (i.occurred_on, i.loss_usd or 0), reverse=True)

    if not fresh:
        log.info("SKIP: nothing new worth publishing")
        state.save(client, config.state_namespace, config.state_key)
        return 0

    if len(fresh) > config.max_posts_per_run:
        log.info("holding back %d incidents over the per-run cap", len(fresh) - config.max_posts_per_run)
        fresh = fresh[: config.max_posts_per_run]

    published, failed = [], 0
    for index, incident in enumerate(fresh):
        line = render(incident)
        if config.dry_run:
            log.info("DRY RUN would post: %s", line)
            published.append(incident.incident_id)
            continue
        try:
            landed, detail = client.post_message(config.room, identity.sign_message(config.room, line))
        except TechnocoreError as error:
            log.error("post failed for %s: %s", incident.protocol, error)
            failed += 1
            continue
        if landed:
            log.info("%s — %s", detail, line)
            published.append(incident.incident_id)
        else:
            log.error("refused for %s: %s", incident.protocol, detail)
            failed += 1
        if index + 1 < len(fresh):
            time.sleep(SECONDS_BETWEEN_POSTS)

    state.record(published)
    if config.dry_run:
        log.info("DRY RUN: state not written")
    elif not state.save(client, config.state_namespace, config.state_key):
        log.warning("state note could not be written; the room remains the source of truth")

    log.info("published %d, failed %d", len(published), failed)
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    started = datetime.now(timezone.utc)
    try:
        return run()
    except ConfigError as error:
        log.error("configuration: %s", error)
        return 2
    except IdentityError as error:
        log.error("identity: %s", error)
        return 2
    except (ResearchError, TechnocoreError) as error:
        log.error("%s", error)
        return 1
    finally:
        log.info("run took %.1fs", (datetime.now(timezone.utc) - started).total_seconds())


if __name__ == "__main__":
    sys.exit(main())
