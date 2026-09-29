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

# Apply security patches + install runtime dependencies
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
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --uid 10001 appuser

# Copy optimized packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appuser app/ ./app/

# Create task_data directory (used at runtime for repo downloads)
RUN mkdir -p /app/task_data && chown -R appuser:appuser /app

# Strip app code
RUN find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /app -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

USER appuser

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
