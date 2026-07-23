# TDMConsole — Web UI edition, for headless/Docker mining.
#
# Build (from the repo root, with the submodule initialised):
#   git submodule update --init --recursive
#   docker build -t tdm-cli .
#
# Run (persist login + settings in a named volume):
#   docker run -d --name tdm -p 8080:8080 -v tdm-data:/data tdm-cli
#   # then open http://localhost:8080
#
# The device-code login shows in the web UI (click "Log in") and in `docker logs`.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# System certs for truststore/SSL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer) from the lock file.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Application code + the pristine upstream submodule.
COPY tdm_cli ./tdm_cli
COPY TwitchDropsMiner ./TwitchDropsMiner
COPY main.py ./

# Freeze the engine's short commit hash for /meta + --version. No .git exists in
# the image, so it is passed in at build time (the CI computes it via
# `git -C TwitchDropsMiner rev-parse --short HEAD`). Falls back to "unknown".
ARG ENGINE_COMMIT=unknown
RUN printf '"""Generated at build time (Docker) — do not edit."""\nENGINE_COMMIT = %s\n' \
    "\"$ENGINE_COMMIT\"" > /app/tdm_cli/_build_info.py

# All runtime state (settings.json, cookies.jar, tdm-cli.json, log.txt, cache/)
# is relocated to /data via TDM_DATA_DIR, so a mounted volume persists it.
ENV TDM_DATA_DIR=/data \
    TDM_ENGINE_DIR=/data/TwitchDropsMiner \
    TDM_WEB_HOST=0.0.0.0 \
    TDM_WEB_PORT=8080 \
    PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /data
VOLUME /data
EXPOSE 8080

# Uses the synced venv python; --mode web needs no TTY. WebUI authentication
# is enabled only when the corresponding environment variables are non-empty.
ENTRYPOINT ["/bin/sh", "-c", "set -- python /app/main.py --mode web \"$@\"; if [ -n \"${USERNAME:-}\" ]; then set -- \"$@\" --username \"$USERNAME\"; fi; if [ -n \"${PASSWORD:-}\" ]; then set -- \"$@\" --password \"$PASSWORD\"; fi; exec \"$@\"", "--"]
