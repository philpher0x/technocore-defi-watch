#!/usr/bin/env bash
#
# Run the agent on this machine with the values in env/.
#
# Sourcing env/github-secrets.env by hand is not enough for two reasons: without
# `set -a` the values stay shell variables that a child process never sees, and
# the PEM is a separate file because it is multi-line. This script handles both.
#
#   scripts/run-local.sh                     dry run against the live search
#   scripts/run-local.sh --fixture           dry run, no OpenAI call at all
#   scripts/run-local.sh --live              really publish to the room
#
# The default is a dry run on purpose: a local mistake publishes to a
# world-readable room under your own identity, and nothing can unpublish it.

set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS="env/github-secrets.env"
PEM="env/TECHNOCORE_IDENTITY_PEM.txt"
PYTHON=".venv/bin/python"

for required in "$SECRETS" "$PEM"; do
    if [[ ! -f "$required" ]]; then
        echo "missing $required — see docs/secrets.md" >&2
        exit 1
    fi
done

if [[ ! -x "$PYTHON" ]]; then
    echo "no interpreter at $PYTHON. Create one with:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# set -a exports everything the file defines, which is the part `source` alone
# does not do.
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a

export TECHNOCORE_IDENTITY_PEM
TECHNOCORE_IDENTITY_PEM="$(cat "$PEM")"

export DRY_RUN=1
FIXTURE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --live)
            DRY_RUN=0
            shift
            ;;
        --fixture)
            if [[ -n "${2:-}" && "${2:-}" != --* ]]; then
                FIXTURE="$2"
                shift 2
            else
                FIXTURE="fixtures/example.json"
                shift
            fi
            ;;
        -h|--help)
            sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -n "$FIXTURE" ]]; then
    export DEFIWATCH_FIXTURE="$FIXTURE"
fi

if [[ "$DRY_RUN" == "0" ]]; then
    echo "LIVE: this will publish to /r/${TECHNOCORE_ROOM:-d-defi-watch} as ${TECHNOCORE_DID:-your key}"
    read -r -p "continue? [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "cancelled"; exit 0; }
fi

exec "$PYTHON" -m defiwatch.main
