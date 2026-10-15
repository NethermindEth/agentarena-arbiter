# Base image: Debian 13 (trixie). Bookworm (Debian 12) carries many unfixed CVEs.
ARG PYTHON_IMAGE=python:3.13.16-slim-trixie

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

# Runtime stage
FROM ${PYTHON_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONOPTIMIZE=2

WORKDIR /app

# Apply all available Debian security updates and drop pip (not needed at runtime)
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/apt /var/log/dpkg.log && \
    python -m pip uninstall -y pip && \
    rm -f /usr/local/bin/pip*

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
