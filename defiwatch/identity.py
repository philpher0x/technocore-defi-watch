"""The Ed25519 identity that signs everything this agent publishes.

Technocore verifies a signature offline against the `did:key` in the message, so
the only thing that travels is the public DID — the private key never leaves the
runner. Two details are easy to get wrong and both fail closed here:

* the signature covers the text *after* the server's single-line sweep, not what
  was typed, so `normalize_text` runs before signing rather than after;
* the server never applies Unicode normalization, so NFC and NFD of one word are
  two different messages. Sign and send the identical bytes.
"""

from __future__ import annotations

import base64
import re
import time
import unicodedata

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519 = b"\xed\x01"
SIGNATURE_CHARS = 86
MAX_MESSAGE_CHARS = 4096

# The categories the server replaces with a space before storing. Cf is the one
# that matters for safety rather than formatting: zero-width joiners, bidi
# overrides and the Unicode tag block are how instructions get smuggled into
# another agent's context, and text that renders as nothing must not survive.
INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})

SIGNATURE_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{SIGNATURE_CHARS}}}")


class IdentityError(RuntimeError):
    """The identity cannot be loaded, or produced something the server rejects."""


def base58btc_encode(data: bytes) -> str:
    """Encode with the base58btc alphabet, preserving leading zero bytes as '1'."""
    leading_zeroes = len(data) - len(data.lstrip(b"\x00"))
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    return "1" * leading_zeroes + encoded


def normalize_text(text: str) -> str:
    """Mirror the server's sweep: invisible categories become spaces, ends trimmed."""
    swept = "".join(
        " " if unicodedata.category(character) in INVISIBLE_CATEGORIES else character
        for character in text
    ).strip()
    if not swept:
        raise IdentityError("message has no visible text after normalization")
    if len(swept) > MAX_MESSAGE_CHARS:
        raise IdentityError(
            f"message is {len(swept)} characters, over the {MAX_MESSAGE_CHARS} cap"
        )
    return swept


def next_nonce() -> str:
    """A wall-clock nonce inside the server's 19-digit limit.

    Signed note writes burn a per-room counter, so a nonce that does not increase
    is refused as a replay. Nanosecond time increases across runs and across
    machines well enough for one agent holding one key.
    """
    return str(time.time_ns())


class Identity:
    """A loaded private key, plus the two things the wire needs: DID and signature."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.did = "did:key:z" + base58btc_encode(MULTICODEC_ED25519 + public_bytes)

    @classmethod
    def from_pem(cls, pem: str, passphrase: str | None = None) -> "Identity":
        """Load an encrypted (or bare) PEM, the shape `technocore_agent.py init` writes."""
        secret = passphrase.encode("utf-8") if passphrase else None
        try:
            loaded = serialization.load_pem_private_key(
                pem.encode("utf-8"), password=secret
            )
        except TypeError as error:
            raise IdentityError(
                "identity is encrypted but TECHNOCORE_IDENTITY_PASSPHRASE is unset"
            ) from error
        except ValueError as error:
            raise IdentityError(
                "identity is not a valid PEM key, or the passphrase is wrong"
            ) from error
        if not isinstance(loaded, Ed25519PrivateKey):
            raise IdentityError("identity must hold an Ed25519 private key")
        return cls(loaded)

    def sign(self, payload: str) -> str:
        """Unpadded base64url over the exact UTF-8 bytes the server will verify."""
        raw = self._private_key.sign(payload.encode("utf-8"))
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        if SIGNATURE_PATTERN.fullmatch(encoded) is None:
            raise IdentityError("produced a signature the server would reject")
        return encoded

    def sign_message(self, room: str, text: str) -> dict:
        """Build the signed-message envelope for `POST /r/<room>`."""
        normalized = normalize_text(text)
        nonce = next_nonce()
        return {
            "did": self.did,
            "sig": self.sign(f"{room}|{nonce}|{normalized}"),
            "nonce": nonce,
            "text": normalized,
        }

    def sign_note(self, namespace: str, key: str, value: str) -> dict:
        """Build the signed-note envelope. Only room-owners and room-allow accept one."""
        normalized = normalize_text(value)
        nonce = next_nonce()
        return {
            "did": self.did,
            "sig": self.sign(f"{namespace}|{key}|{nonce}|{normalized}"),
            "nonce": nonce,
            "value": normalized,
        }
