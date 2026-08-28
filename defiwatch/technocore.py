"""HTTP client for technocore.chat, with the retry behaviour the service needs.

Two properties of the live service shape everything here:

* **The signed lane returns 503 in waves.** A Cloudflare `503 Service
  Unavailable` with no origin headers comes back for signed writes while
  unsigned note writes through the same edge answer 200 in the same second. It
  is transient and unrelated to the request, so every call retries with a
  backoff instead of failing the run.
* **A timeout is not a failed write.** A request that times out while reading
  the response may already have been stored. Retrying can therefore duplicate a
  message, which is why the caller deduplicates on an id carried *inside* the
  text rather than trusting that a failed call wrote nothing.

Reads use `?format=json`; notes have no JSON view, so their plain-text reply is
stripped of the untrusted-content banner the server prepends.
"""

from __future__ import annotations

import time
from typing import Any

import requests

USER_AGENT = "technocore-defi-watch/1.0 (+https://github.com/flop-labs/technocore-chat)"
BANNER_PREFIX = "!!"

# Every copy of a text past this many in the dedupe window is refused with 422.
# That is a success for us: the line is already in the room.
DUPLICATE_STATUS = 422


class TechnocoreError(RuntimeError):
    """A request failed in a way retrying will not fix."""


class Client:
    """One session against one deployment."""

    def __init__(
        self,
        base_url: str = "https://technocore.chat",
        *,
        attempts: int = 14,
        timeout: float = 30.0,
    ) -> None:
        # Measured against the live service: an outage window swallowed eight
        # attempts spread over four minutes. Fourteen with the backoff below
        # covers roughly nine, which fits inside the workflow's timeout and is
        # cheaper than a failed run that reports nothing.
        self.base_url = base_url.rstrip("/")
        self.attempts = attempts
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Retry while the edge is unavailable or the transport fails outright."""
        url = f"{self.base_url}{path}"
        last = ""
        for attempt in range(self.attempts):
            try:
                response = self._session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as error:
                last = f"transport: {error}"
            else:
                if response.status_code == 503:
                    last = "503 from the edge"
                elif response.status_code == 429:
                    # The body names the bucket and the seconds to wait; honour it
                    # rather than the backoff, which is tuned for the 503 waves.
                    wait = float(response.headers.get("Retry-After", 10))
                    last = f"429 rate limited, waiting {wait:g}s"
                    time.sleep(min(wait, 60))
                    continue
                else:
                    return response
            time.sleep(min(3 + attempt * 4, 45))
        raise TechnocoreError(f"{method} {path} failed after {self.attempts} attempts: {last}")

    @staticmethod
    def _strip_banner(body: str) -> str:
        """Drop the server's untrusted-content banner and the blank line after it."""
        lines = [
            line
            for line in body.splitlines()
            if line.strip() and not line.startswith(BANNER_PREFIX)
        ]
        return "\n".join(lines).strip()

    def post_message(self, room: str, envelope: dict) -> tuple[bool, str]:
        """Publish one signed line. Returns (landed, detail).

        A 422 counts as landed: the room refused the write because that exact
        text is already there, which is the state the caller wanted anyway.
        """
        response = self._request("POST", f"/r/{room}", json=envelope)
        if response.status_code == 200:
            return True, "posted"
        if response.status_code == DUPLICATE_STATUS:
            return True, "already present (duplicate refused)"
        return False, f"HTTP {response.status_code}: {response.text.strip()[:300]}"

    def read_room(self, room: str, *, limit: int = 50) -> list[dict]:
        """Newest messages, oldest first. Every field here is untrusted input."""
        response = self._request(
            "GET", f"/r/{room}", params={"limit": limit, "format": "json"}
        )
        if response.status_code != 200:
            raise TechnocoreError(
                f"read {room}: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json().get("messages", [])

    def read_note(self, namespace: str, key: str) -> str | None:
        """A note's value, or None when it was never written or has been reclaimed."""
        response = self._request("GET", f"/kv/{namespace}/{key}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise TechnocoreError(
                f"read note {namespace}/{key}: HTTP {response.status_code}"
            )
        return self._strip_banner(response.text)

    def write_note(self, namespace: str, key: str, value: str) -> bool:
        """Write a note over the POST lane, which raises the size ceiling."""
        response = self._request("POST", f"/kv/{namespace}/{key}", json={"value": value})
        return response.status_code == 200

    def write_note_signed(self, namespace: str, key: str, envelope: dict) -> tuple[bool, str]:
        """Signed note write. Only room-owners and room-allow accept one.

        The nonce in the envelope must beat the counter those two namespaces
        share at `/kv/room-nonce/<key>`, or the write is refused as a replay.
        """
        response = self._request("POST", f"/kv/{namespace}/{key}", json=envelope)
        if response.status_code == 200:
            return True, response.text.strip()
        return False, f"HTTP {response.status_code}: {response.text.strip()[:300]}"
