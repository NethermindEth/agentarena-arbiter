# Base images: Debian 13 (trixie). Bookworm (Debian 12) carries many unfixed CVEs.
# The build stage uses the official Python image; the runtime stage is distroless, which has
# no shell, package manager, coreutils, util-linux, PAM, perl, ncurses or systemd libraries.
# Every finding in the trixie-slim scan came from packages like these that Debian has not
# fixed yet, so the runtime image leaves them out instead of upgrading them.
ARG PYTHON_IMAGE=python:3.13.16-slim-trixie
ARG RUNTIME_IMAGE=gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2

FROM ${RUNTIME_IMAGE} AS runtime-base

# Build stage
FROM ${PYTHON_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONOPTIMIZE=2

WORKDIR /build

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y pip

# Strip unnecessary files from site-packages
RUN find /usr/local/lib/python3.13/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.13/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.13/site-packages -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete && \
    find /usr/local/lib/python3.13/site-packages -type f \( \
        -name "*.dist-info/RECORD" -o \
        -name "*.dist-info/top_level.txt" -o \
        -name "*.dist-info/entry_points.txt" \
    \) -delete

# Drop the stdlib extension modules whose system libraries the runtime image does not ship
# (ncurses, readline, gdbm, Berkeley DB, SQLite, libuuid, Tk), plus CPython's test modules.
# The app uses none of them; `uuid` falls back to its pure-Python implementation.
RUN cd /usr/local/lib/python3.13 && \
    rm -f lib-dynload/_curses*.so lib-dynload/readline*.so lib-dynload/_dbm*.so \
          lib-dynload/_gdbm*.so lib-dynload/_sqlite3*.so lib-dynload/_uuid*.so \
          lib-dynload/_tkinter*.so lib-dynload/_test*.so lib-dynload/_xxtestfuzz*.so \
          lib-dynload/xx*.so && \
    rm -rf ensurepip idlelib tkinter turtledemo test

# Assemble the files the runtime image needs on top of distroless/cc, which already has
# libc, OpenSSL, zlib, zstd, libgcc and libstdc++. CPython also links libbz2, libffi and liblzma.
# Their dpkg records go in status.d, as distroless does it, so image scanners still see them.
COPY --from=runtime-base /etc/passwd /etc/group /tmp/distroless-etc/
RUN apt-get update && \
    apt-get install -y --only-upgrade --no-install-recommends libbz2-1.0 libffi8 liblzma5 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /rootfs/usr/lib/x86_64-linux-gnu /rootfs/var/lib/dpkg/status.d /rootfs/etc \
             /rootfs/app/task_data && \
    for pkg in libbz2-1.0 libffi8 liblzma5; do \
        dpkg -L "$pkg" | grep -E '/lib/x86_64-linux-gnu/[^/]+\.so[^/]*$' | \
            xargs -I{} cp -a {} /rootfs/usr/lib/x86_64-linux-gnu/ && \
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
    TZ=UTC

WORKDIR /app

# Python interpreter, stdlib and installed packages
COPY --from=builder /usr/local /usr/local

# Extra system libraries, their dpkg records and the non-root user
COPY --from=builder /rootfs/usr /usr
COPY --from=builder /rootfs/var /var
COPY --from=builder /rootfs/etc/passwd /rootfs/etc/group /etc/

# Application code, plus task_data (used at runtime for repo downloads)
COPY --from=builder --chown=10001:10001 /rootfs/app /app

USER 10001:10001

EXPOSE 8004

CMD ["/usr/local/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
