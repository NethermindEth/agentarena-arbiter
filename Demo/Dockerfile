# Base images: Debian 13 (trixie). Bookworm (Debian 12) carries many unfixed CVEs.
# The runtime is debian:trixie-slim rather than distroless: the findings-triage agents need a
# shell and coreutils, Node.js (npm projects and the Claude Code CLI), bubblewrap and socat (the
# command sandbox), git and a compiler toolchain (dependency builds), and Foundry (PoCs).
# Python and Node.js come from their official images; the build stage works out which Debian
# packages the interpreter links against, and the runtime installs only those plus the tools.
#
# Every base is pinned by tag and digest: fixes arrive as re-pushes of the same tag, and only a
# digest change lets Dependabot propose the rebuild. The Python patch version is also pinned in
# each job of .github/workflows/ci-tests.yaml (python-version); the two move together.
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS runtime-base

# Node.js, npm and the Claude Code CLI. The CLI is PINNED: the evaluator depends on its flag and
# sandbox-setting semantics (--bare, --restricted, --permission-prompts, --json-schema, sandbox.*),
# which were verified against this exact version; re-verify before bumping it. Only the node
# binary and the global node_modules are kept, with the bin links npm created.
FROM node:24.22.3-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS node
RUN npm install -g @anthropic-ai/claude-code@2.1.328 && claude --version && \
    mkdir -p /out/bin /out/lib && \
    cp -a /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/claude /out/bin/ && \
    cp -a /usr/local/lib/node_modules /out/lib/

# Foundry for Solidity PoCs, from a release archive pinned by version and checksum (the image is
# linux/amd64 only), so a rebuild cannot change PoC behaviour. Dependabot does not read these, so
# a bump changes both by hand, with the checksum from the release's .sha256 asset. It is installed
# outside any HOME: triage agents may not read the service user's HOME, and each agent gets its own
# private HOME for compiler caches. (Add other stacks the same way, e.g. Rust under /opt.)
FROM runtime-base AS foundry
ARG FOUNDRY_VERSION=v1.9.0
ARG FOUNDRY_SHA256=8d17599b42e3139f1a9c171be3798c4ed8e743ae17b84cc3435e57bf2a51ae99
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSLo /tmp/foundry.tar.gz \
        "https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_amd64.tar.gz" && \
    echo "${FOUNDRY_SHA256}  /tmp/foundry.tar.gz" | sha256sum -c - && \
    mkdir -p /opt/foundry/bin && \
    tar -xzf /tmp/foundry.tar.gz -C /opt/foundry/bin && \
    rm /tmp/foundry.tar.gz && \
    /opt/foundry/bin/forge --version

# Build stage
FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder

# Pinned by digest because a tag can be moved to another binary. The digest
# wins over the tag, so a uv bump changes both.
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /bin/uv

# The environment is built from the lockfile alone, outside /app. This stage has no compiler:
# every locked package ships a wheel, and UV_NO_BUILD fails the build on a package without one.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    UV_NO_BUILD=1

WORKDIR /build

COPY pyproject.toml uv.lock ./
# --locked fails the build when uv.lock no longer matches pyproject.toml.
RUN uv sync --locked --no-dev --no-install-project

# The venv has no pip; the base interpreter's pip is never run but is scanned.
RUN /usr/local/bin/python -m pip uninstall -y pip

# Strip unnecessary files from site-packages
RUN site=/opt/venv/lib/python3.14/site-packages && \
    find "$site" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find "$site" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find "$site" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

# Drop the stdlib extension modules the app never uses (curses, readline, dbm, SQLite, the C
# uuid, Tk), plus CPython's test modules and the wheels ensurepip bundles, so the runtime needs
# none of their system libraries. `uuid` falls back to its pure-Python implementation.
RUN cd /usr/local/lib/python3.14 && \
    rm -f lib-dynload/_curses*.so lib-dynload/readline*.so lib-dynload/_dbm*.so \
          lib-dynload/_gdbm*.so lib-dynload/_sqlite3*.so lib-dynload/_uuid*.so \
          lib-dynload/_tkinter*.so lib-dynload/_test*.so lib-dynload/_ctypes_test*.so \
          lib-dynload/_xxtestfuzz*.so \
          lib-dynload/xx*.so && \
    rm -rf ensurepip idlelib tkinter turtledemo test

# The Debian packages owning the shared libraries that the interpreter and the locked wheels
# link against, as the official Python image works out its own runtime packages.
RUN find /usr/local /opt/venv -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) \
        -exec ldd '{}' ';' 2>/dev/null | \
    awk '/=>/ { so = $(NF-1); if (so ~ "^/(usr/local|opt/venv)/") next; gsub("^/(usr/)?", "", so); printf "*%s\n", so }' | \
    sort -u | xargs -r dpkg-query --search 2>/dev/null | cut -d: -f1 | sort -u > /runtime-packages.txt && \
    cat /runtime-packages.txt && \
    test -s /runtime-packages.txt

# Runtime stage
FROM runtime-base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=2 \
    HOME=/app \
    TZ=UTC \
    PATH="/opt/venv/bin:/opt/foundry/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DISABLE_AUTOUPDATER=1 \
    TRIAGE_WORK_DIR=/var/lib/arbiter-triage

# The interpreter's shared libraries (listed by the build stage), and what the triage agents and
# their sandbox run: bubblewrap (>= 0.8.0, not setuid) and socat are the Claude Code command
# sandbox, which refuses to start without them (sandbox.failIfUnavailable); git, curl and
# build-essential let the build agent fetch dependencies and compile projects.
COPY --from=builder /runtime-packages.txt /tmp/runtime-packages.txt
RUN apt-get update && \
    apt-get install -y --no-install-recommends $(cat /tmp/runtime-packages.txt) \
        ca-certificates netbase bubblewrap socat git curl build-essential && \
    rm -rf /var/lib/apt/lists/* /tmp/runtime-packages.txt

# Python interpreter and stdlib, the locked environment (its python is a symlink into
# /usr/local), Node.js with npm and the Claude Code CLI, and Foundry
COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt/venv /opt/venv
COPY --from=node /out/ /usr/local/
COPY --from=foundry /opt/foundry/bin /opt/foundry/bin
RUN python --version && node --version && npm --version && claude --version && \
    forge --version && bwrap --version && socat -V | head -n 2

# The non-root service user. The application code stays root-owned and read-only for it: the
# triage agents run as this user, so they must not be able to change the service. It writes only
# to task_data (repository downloads) and the triage work directory (sandboxes and PoC evidence),
# which lies outside HOME and the service directory because agents may read neither.
RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin appuser && \
    mkdir -p /app/task_data /var/lib/arbiter-triage && \
    chown 10001:10001 /app/task_data /var/lib/arbiter-triage && \
    chmod 700 /var/lib/arbiter-triage

WORKDIR /app

# Application code
COPY app/ /app/app/

USER 10001:10001

EXPOSE 8004

CMD ["/opt/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
