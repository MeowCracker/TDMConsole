#!/usr/bin/env bash
# Convenience launcher — runs the CLI via uv from the repo root.
# Any args are forwarded, e.g.:  ./run.sh -v --log
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f TwitchDropsMiner/twitch.py ]; then
    echo "Submodule missing — initialising..." >&2
    git submodule update --init --recursive
fi

exec uv run main.py "$@"
