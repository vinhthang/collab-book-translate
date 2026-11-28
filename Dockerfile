FROM python:3.12-slim

LABEL org.opencontainers.image.version="0.9.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8766 \
    HOST=0.0.0.0 \
    COLLAB_DATA_DIR=/data \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-core \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY scripts ./scripts
COPY plugin.json mcp_config.json config.example.json ./

RUN groupadd -r collab && useradd -r -g collab -u 10001 -d /app collab \
    && mkdir -p /data \
    && chown -R collab:collab /app /data

# Note: when upgrading a persistent host /data mount created under v0.9.0 (root),
# run `chown -R 10001:10001 /data` on the host before starting v0.9.1.
USER collab

EXPOSE 8766

CMD ["/app/.venv/bin/python", "-m", "scripts.mcp_server.app"]
