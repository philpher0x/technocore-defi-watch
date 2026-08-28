#!/usr/bin/env python3
"""Mint a CI-only key and let it write to the room, without shipping your identity.

An owned `d-` room accepts signed writes from its owner plus every key listed at
`/kv/room-allow/<room>`. This script creates a fresh Ed25519 key, adds it to that
list with a write signed by the owner, and leaves you a PEM to paste into
`TECHNOCORE_IDENTITY_PEM`. If the runner is ever compromised, the blast radius is
one room's write access rather than the identity that owns it.

Revoking is the same command's inverse: rewrite the allow-list without that DID.

    python scripts/authorize_writer.py \\
        --owner-pem ../technocore-my-project/identity.pem \\
        --room d-defi-watch \\
        --out env/ci-writer.pem

Run it locally. The owner key must never reach CI — that is the entire point.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from defiwatch.identity import Identity, next_nonce  # noqa: E402
from defiwatch.technocore import Client  # noqa: E402


def read_allow_list(client: Client, room: str) -> list[str]:
    current = client.read_note("room-allow", room)
    return current.split() if current else []


def highest_burnt_nonce(client: Client, room: str) -> int:
    """room-owners and room-allow share one replay counter, at /kv/room-nonce/<room>."""
    raw = client.read_note("room-nonce", room)
    try:
        return int((raw or "0").strip())
    except ValueError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-pem", required=True, type=Path)
    parser.add_argument("--room", default="d-defi-watch")
    parser.add_argument("--out", required=True, type=Path, help="where to write the new key")
    parser.add_argument("--base-url", default="https://technocore.chat")
    parser.add_argument("--revoke", metavar="DID", help="remove this DID instead of adding one")
    args = parser.parse_args()

    owner_passphrase = getpass.getpass(f"Passphrase for {args.owner_pem}: ")
    owner = Identity.from_pem(args.owner_pem.read_text(encoding="utf-8"), owner_passphrase)
    client = Client(args.base_url)

    owner_note = client.read_note("room-owners", args.room)
    if owner_note != owner.did:
        print(f"/r/{args.room} is owned by {owner_note or 'nobody'}, not by {owner.did}")
        return 1

    allowed = read_allow_list(client, args.room)

    if args.revoke:
        if args.revoke not in allowed:
            print(f"{args.revoke} is not on the allow-list; nothing to do")
            return 0
        allowed = [did for did in allowed if did != args.revoke]
        new_did = None
    else:
        if args.out.exists():
            print(f"{args.out} already exists — refusing to overwrite a key")
            return 1
        writer_passphrase = secrets.token_urlsafe(24)
        writer_key = Ed25519PrivateKey.generate()
        args.out.write_bytes(
            writer_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(
                    writer_passphrase.encode("utf-8")
                ),
            )
        )
        args.out.chmod(0o600)
        new_did = Identity(writer_key).did
        allowed = sorted(set(allowed) | {new_did})

    # The nonce has to beat the counter both ownership namespaces share.
    nonce = str(max(int(next_nonce()), highest_burnt_nonce(client, args.room) + 1))
    value = " ".join(allowed)
    envelope = {
        "did": owner.did,
        "sig": owner.sign(f"room-allow|{args.room}|{nonce}|{value}"),
        "nonce": nonce,
        "value": value,
    }
    written, detail = client.write_note_signed("room-allow", args.room, envelope)
    if not written:
        print(f"allow-list write refused: {detail}")
        return 1

    print(f"allow-list for /r/{args.room} is now: {value or '(empty)'}")
    if new_did:
        print(f"\nnew writer DID : {new_did}")
        print(f"key written to : {args.out}")
        print(f"passphrase     : {writer_passphrase}")
        print("\nPut the PEM in TECHNOCORE_IDENTITY_PEM, the passphrase in")
        print("TECHNOCORE_IDENTITY_PASSPHRASE, and the DID in TECHNOCORE_DID.")
        print("This passphrase is shown once and is not stored anywhere else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
