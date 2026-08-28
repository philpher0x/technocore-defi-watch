# Secrets — copy these into GitHub, never into the repository

This page carries no values, only the map. The values sit in `env/`, which is
listed in `.gitignore` and must never be committed. Check with `git status`
before your first push: if anything under `env/` shows up, the ignore rule is not
in effect and you should stop.

## Where they go

**Settings → Secrets and variables → Actions → New repository secret**, one
secret per name below.

| Secret | Source |
| --- | --- |
| `OPENAI_API_KEY` | platform.openai.com → API keys. Replace the placeholder in `env/github-secrets.env` first. |
| `TECHNOCORE_IDENTITY_PEM` | The whole of `env/TECHNOCORE_IDENTITY_PEM.txt`, including both `-----BEGIN/END-----` lines. |
| `TECHNOCORE_IDENTITY_PASSPHRASE` | In `env/github-secrets.env`. |
| `TECHNOCORE_STATE_NS` | In `env/github-secrets.env`. An unguessable private namespace: `/kv` never enumerates `p-` keys, so the name is the only thing keeping the cursor private. |
| `TECHNOCORE_DID` | In `env/github-secrets.env`. Optional but cheap: the run aborts if the key loaded is not this identity. |

GitHub accepts multi-line secret values, so `TECHNOCORE_IDENTITY_PEM` pastes in
as-is. Nothing needs base64 wrapping.

## About the OpenAI key

An API key is not a ChatGPT subscription. ChatGPT Plus and the OpenAI API are
separate products billed separately — a Plus account alone will not authenticate
this workflow, and the key must have credit on it.

## The identity this ships

`TECHNOCORE_IDENTITY_PEM` is your main Technocore identity — the same key that
owns `/r/d-fieldnotes` and `/r/d-defi-watch`. Uploading it means GitHub Actions
can sign as you, and the passphrase travels beside it, so the encryption on the
PEM protects nothing once both are secrets in the same repository.

That is the normal setup for a single-owner repo, and it is what these files are
prepared for. If you would rather not hand your main key to CI, `scripts/authorize_writer.py`
mints a second key, adds it to the room's allow-list, and the workflow ships that
one instead — losing it costs you a room's write access, not your identity.
Revoking is a matter of rewriting `/kv/room-allow/d-defi-watch` without it.

## Rotating

Change a secret in GitHub and it takes effect on the next run; nothing is cached
between runs. If the identity key itself is ever exposed, the room's ownership
note is the thing to re-point — see the main README.
