# Base images: Debian 13 (trixie). Bookworm (Debian 12) carries many unfixed CVEs.
# The build stage uses the official Python image; the runtime stage is distroless, which has
# no shell, package manager, coreutils, util-linux, PAM, perl, ncurses or systemd libraries.
# Every finding in the trixie-slim scan came from packages like these that Debian has not
# fixed yet, so the runtime image leaves them out instead of upgrading them.
#
# Both bases are pinned by tag and digest: fixes arrive as re-pushes of the same tag, and only
# a digest change lets Dependabot propose the rebuild. The Python patch version is also pinned
# in each job of .github/workflows/ci-tests.yaml (python-version); the two move together.
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2 AS runtime-base

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
    find "$site" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete && \
    find "$site" -type f \( \
        -name "*.dist-info/RECORD" -o \
        -name "*.dist-info/top_level.txt" -o \
        -name "*.dist-info/entry_points.txt" \
    \) -delete

# Drop the stdlib extension modules whose system libraries the runtime image does not ship
# (ncurses, readline, gdbm, Berkeley DB, SQLite, libuuid, Tk), plus CPython's test modules and
# the wheels ensurepip bundles. The app uses none of them; `uuid` falls back to its pure-Python
# implementation.
RUN cd /usr/local/lib/python3.14 && \
    rm -f lib-dynload/_curses*.so lib-dynload/readline*.so lib-dynload/_dbm*.so \
          lib-dynload/_gdbm*.so lib-dynload/_sqlite3*.so lib-dynload/_uuid*.so \
          lib-dynload/_tkinter*.so lib-dynload/_test*.so lib-dynload/_xxtestfuzz*.so \
          lib-dynload/xx*.so && \
    rm -rf ensurepip idlelib tkinter turtledemo test

# Assemble the files the runtime image needs on top of distroless/cc, which already has
# libc, OpenSSL, zlib, zstd, libgcc and libstdc++. CPython also links libbz2, libffi and liblzma.
# Their dpkg records go in status.d, as distroless does it, so image scanners still see them.
# The multiarch directory comes from the interpreter, so the same file builds on amd64 and
# arm64 hosts.
COPY --from=runtime-base /etc/passwd /etc/group /tmp/distroless-etc/
RUN multiarch=$(python -c "import sysconfig; print(sysconfig.get_config_var('MULTIARCH'))") && \
    apt-get update && \
    apt-get install -y --only-upgrade --no-install-recommends libbz2-1.0 libffi8 liblzma5 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p "/rootfs/usr/lib/$multiarch" /rootfs/var/lib/dpkg/status.d /rootfs/etc \
             /rootfs/app/task_data && \
    for pkg in libbz2-1.0 libffi8 liblzma5; do \
        dpkg -L "$pkg" | grep -E "/lib/$multiarch/[^/]+\.so[^/]*$" | \
            xargs -I{} cp -a {} "/rootfs/usr/lib/$multiarch/" && \
        dpkg-query -s "$pkg" > "/rootfs/var/lib/dpkg/status.d/$pkg"; \
    done && \
    cp /tmp/distroless-etc/passwd /tmp/distroless-etc/group /rootfs/etc/ && \
    echo "appuser:x:10001:10001::/app:/sbin/nologin" >> /rootfs/etc/passwd && \
    echo "appuser:x:10001:" >> /rootfs/etc/group

# Copy application code and strip bytecode caches
COPY app/ /rootfs/app/app/
RUN find /rootfs/app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /rootfs/app -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

# Runtime stage
FROM runtime-base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=2 \
    HOME=/app \
    TZ=UTC \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"

WORKDIR /app

# Python interpreter and stdlib
COPY --from=builder /usr/local /usr/local

# The locked environment; its python is a symlink into /usr/local
COPY --from=builder /opt/venv /opt/venv

# Extra system libraries, their dpkg records and the non-root user
COPY --from=builder /rootfs/usr /usr
COPY --from=builder /rootfs/var /var
COPY --from=builder /rootfs/etc/passwd /rootfs/etc/group /etc/

# Application code, plus task_data (used at runtime for repo downloads)
COPY --from=builder --chown=10001:10001 /rootfs/app /app

USER 10001:10001

EXPOSE 8004

CMD ["/opt/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
