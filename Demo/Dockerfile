# Build stage
FROM python:3.13.5-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONOPTIMIZE=2

WORKDIR /build

# Apply security patches
RUN apt-get update && \
    apt-get install -y --only-upgrade \
    gpgv \
    libc-bin \
    libc6 \
    libgnutls30 \
    libpam-modules \
    libpam-modules-bin \
    libpam-runtime \
    libpam0g \
    libsqlite3-0 \
    libssl3 \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Strip unnecessary files from site-packages
RUN find /usr/local/lib/python3.13/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.13/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.13/site-packages -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete && \
    find /usr/local/lib/python3.13/site-packages -type f \( \
        -name "*.dist-info/RECORD" -o \
        -name "*.dist-info/top_level.txt" -o \
        -name "*.dist-info/entry_points.txt" \
    \) -delete

# Runtime stage
FROM python:3.13.5-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONOPTIMIZE=2

WORKDIR /app

# Install system dependencies.
#  - Node.js 22 (via NodeSource) runs Anthropic's Claude Code CLI, which the evaluator drives as
#    hardened subprocesses (app/core/claude_code_cli.py).
#  - bubblewrap + socat are the Claude Code command sandbox on Linux: triage agents compile and
#    run PoCs only inside it (sandbox.failIfUnavailable: no sandbox => no PoC execution).
#  - git/curl/build-essential let the build agent fetch dependencies and compile projects.
RUN apt-get update && \
    apt-get install -y --only-upgrade \
        gpgv \
        libc-bin \
        libc6 \
        libgnutls30 \
        libpam-modules \
        libpam-modules-bin \
        libpam-runtime \
        libpam0g \
        libsqlite3-0 \
        libssl3 \
        openssl \
        perl-base && \
    apt-get install -y --no-install-recommends \
        build-essential \
        git  \
        curl \
        ca-certificates \
        gnupg \
        bubblewrap \
        socat \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (npm), PINNED: the evaluator depends on its flag and sandbox-setting
# semantics (--bare, --restricted, --permission-prompts, --json-schema, sandbox.*), which were
# verified against this exact version. Re-verify before bumping it. Invoked as `claude`
# (CLAUDE_CMD) and authenticated at runtime via CLAUDE_API_KEY (passed as ANTHROPIC_API_KEY).
RUN npm install -g @anthropic-ai/claude-code@2.1.328 && claude --version
ENV CLAUDE_CMD=claude DISABLE_AUTOUPDATER=1

# Foundry toolchain for Solidity PoCs, installed OUTSIDE any HOME: triage agents may not read
# the service user's HOME, and each agent gets its own private HOME for compiler caches.
# (Add other stacks the same way, e.g. Rust via RUSTUP_HOME/CARGO_HOME under /opt.)
ENV FOUNDRY_DIR=/opt/foundry
ENV PATH=/opt/foundry/bin:$PATH
RUN curl -fsSL https://foundry.paradigm.xyz | bash && /opt/foundry/bin/foundryup && forge --version

# Triage sandboxes live outside /app and outside HOME (agents may read neither).
ENV TRIAGE_WORK_DIR=/var/lib/arbiter-triage

# Create .dockerenv file to help with environment detection
RUN touch /.dockerenv

# Create non-root user
RUN useradd --create-home --uid 10001 appuser

# Copy optimized packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
# Root-owned and read-only for appuser: agents run as appuser, so the service code must not
# be writable by it.
COPY app/ ./app/

# Create task_data directory (used at runtime for repo downloads) and the triage work dir
RUN mkdir -p /app/task_data /var/lib/arbiter-triage && \
    chown -R appuser:appuser /app/task_data && \
    chown appuser:appuser /var/lib/arbiter-triage && chmod 700 /var/lib/arbiter-triage

# Strip app code
RUN find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /app -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

USER appuser

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
