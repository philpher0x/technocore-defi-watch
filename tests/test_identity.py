"""The signing path, checked against fixed vectors.

The DID below was produced by this code and independently by the reference
implementation in `flop-labs`' starter tool from the same seed. If a refactor
changes either the multicodec prefix or the base58 encoding, this test fails
before the server does.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from defiwatch.identity import Identity, IdentityError, normalize_text

SEED = bytes(range(32))
EXPECTED_DID = "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd"


@pytest.fixture
def identity() -> Identity:
    return Identity(Ed25519PrivateKey.from_private_bytes(SEED))


def test_did_matches_the_reference_encoding(identity: Identity) -> None:
    assert identity.did == EXPECTED_DID
    assert len(identity.did) == 56


def test_signature_verifies_against_the_published_key(identity: Identity) -> None:
    payload = "d-defi-watch|123|hello"
    signature = identity.sign(payload)

    assert len(signature) == 86
    raw = base64.urlsafe_b64decode(signature + "==")
    public_key = Ed25519PrivateKey.from_private_bytes(SEED).public_key()
    public_key.verify(raw, payload.encode("utf-8"))  # raises if it does not verify


def test_message_envelope_signs_the_normalized_text(identity: Identity) -> None:
    """The server sweeps before verifying, so the signature must cover the sweep."""
    envelope = identity.sign_message("d-defi-watch", "line\twith​invisibles")

    assert envelope["text"] == "line with invisibles"
    raw = base64.urlsafe_b64decode(envelope["sig"] + "==")
    payload = f"d-defi-watch|{envelope['nonce']}|{envelope['text']}".encode("utf-8")
    Ed25519PrivateKey.from_private_bytes(SEED).public_key().verify(raw, payload)


def test_note_envelope_covers_namespace_and_key(identity: Identity) -> None:
    envelope = identity.sign_note("room-owners", "d-defi-watch", identity.did)

    raw = base64.urlsafe_b64decode(envelope["sig"] + "==")
    payload = f"room-owners|d-defi-watch|{envelope['nonce']}|{envelope['value']}".encode("utf-8")
    Ed25519PrivateKey.from_private_bytes(SEED).public_key().verify(raw, payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  padded  ", "padded"),
        ("tab\tseparated", "tab separated"),
        ("zero​width", "zero width"),
        ("bidi‮override", "bidi override"),
    ],
)
def test_normalization_mirrors_the_server_sweep(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_empty_after_sweep_is_refused() -> None:
    with pytest.raises(IdentityError):
        normalize_text("​​")


def test_oversize_message_is_refused() -> None:
    with pytest.raises(IdentityError):
        normalize_text("x" * 4097)


def _encrypted_pem(passphrase: str) -> str:
    return (
        Ed25519PrivateKey.from_private_bytes(SEED)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(
                passphrase.encode("utf-8")
            ),
        )
        .decode("ascii")
    )


def test_encrypted_pem_round_trips_with_its_passphrase() -> None:
    loaded = Identity.from_pem(_encrypted_pem("correct horse battery"), "correct horse battery")

    assert loaded.did == EXPECTED_DID


def test_encrypted_pem_without_passphrase_explains_itself() -> None:
    with pytest.raises(IdentityError, match="PASSPHRASE"):
        Identity.from_pem(_encrypted_pem("correct horse battery"))


def test_wrong_passphrase_is_reported_as_such() -> None:
    with pytest.raises(IdentityError, match="passphrase"):
        Identity.from_pem(_encrypted_pem("correct horse battery"), "wrong")
