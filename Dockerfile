# syntax=docker/dockerfile:1

# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed only during build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a prefix so we can copy them cleanly
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="xray-agent" \
      org.opencontainers.image.description="JFrog Xray auto-remediation AI agent" \
      org.opencontainers.image.source="https://github.com/your-org/xray-agent"

# Non-root user
RUN groupadd --gid 1001 agent \
 && useradd --uid 1001 --gid agent --shell /bin/bash --create-home agent

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=agent:agent agent/      ./agent/
COPY --chown=agent:agent integrations/ ./integrations/
COPY --chown=agent:agent tools/      ./tools/
COPY --chown=agent:agent api/        ./api/
COPY --chown=agent:agent config.py   ./config.py

USER agent

# Health check via the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

# Use Gunicorn + Uvicorn workers for production throughput
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "2", "--log-level", "info", "--no-access-log"]
 