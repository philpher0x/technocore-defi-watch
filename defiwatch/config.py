"""Every knob this agent reads, in one place, resolved from the environment.

Secrets come from GitHub Actions secrets; the tuning values are plain `env:`
entries in the workflow so a change to cadence or thresholds is a diff anyone
can read, not a hidden setting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """A required setting is missing or unusable."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Secrets live in Settings > Secrets and variables > "
            f"Actions; see env/README.md for the full list."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _number(name: str, default: float) -> float:
    raw = _optional(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from error


def _flag(name: str, default: bool = False) -> bool:
    raw = _optional(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    identity_pem: str
    identity_passphrase: str
    expected_did: str
    base_url: str
    room: str
    state_namespace: str
    state_key: str
    lookback_hours: float
    max_posts_per_run: int
    min_loss_usd: float
    search_domains: list[str] = field(default_factory=list)
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        dry_run = _flag("DRY_RUN")
        # A dry run still signs and still searches; it just never writes. The
        # OpenAI key stays mandatory unless a fixture replaces the search, which
        # is how the pipeline is exercised without spending anything.
        replaying = bool(_optional("DEFIWATCH_FIXTURE"))
        domains = [d.strip() for d in _optional("SEARCH_DOMAINS").split(",") if d.strip()]
        return cls(
            openai_api_key=_optional("OPENAI_API_KEY") if replaying
            else _required("OPENAI_API_KEY"),
            openai_model=_optional("OPENAI_MODEL", "gpt-5.6"),
            identity_pem=_required("TECHNOCORE_IDENTITY_PEM"),
            identity_passphrase=_optional("TECHNOCORE_IDENTITY_PASSPHRASE"),
            expected_did=_optional("TECHNOCORE_DID"),
            base_url=_optional("TECHNOCORE_BASE_URL", "https://technocore.chat"),
            room=_optional("TECHNOCORE_ROOM", "d-defi-watch"),
            state_namespace=_required("TECHNOCORE_STATE_NS") if not dry_run
            else _optional("TECHNOCORE_STATE_NS", "p-dry-run-placeholder"),
            state_key=_optional("TECHNOCORE_STATE_KEY", "defi-watch-state"),
            lookback_hours=_number("LOOKBACK_HOURS", 24),
            max_posts_per_run=int(_number("MAX_POSTS_PER_RUN", 3)),
            min_loss_usd=_number("MIN_LOSS_USD", 250_000),
            search_domains=domains,
            dry_run=dry_run,
        )
