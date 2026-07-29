FROM python:3.13.5-slim

WORKDIR /app

# Install system dependencies. Node.js 22 (via NodeSource) is required for Anthropic's
# Claude Code CLI, which the evaluator drives as a read-only subprocess; git/curl/ca-certs
# support fetching the CLI and general repo tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (npm). The evaluator invokes it as `claude` (see CLAUDE_CMD) and
# authenticates it at runtime via CLAUDE_API_KEY (passed through as ANTHROPIC_API_KEY).
RUN npm install -g @anthropic-ai/claude-code && claude --version
ENV CLAUDE_CMD=claude

# Create .dockerenv file to help with environment detection
RUN touch /.dockerenv

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose the port the app runs on
EXPOSE 8004

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"] 