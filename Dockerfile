FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8766 \
    HOST=0.0.0.0 \
    COLLAB_DATA_DIR=/data

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

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "ebooklib>=0.18" \
    "beautifulsoup4>=4.12.0" \
    "python-docx>=1.1.0" \
    "mobi>=0.3.3" \
    "markdown>=3.6" \
    "lxml>=5.2.0" \
    "pypinyin>=0.51.0" \
    "pymupdf>=1.24.0" \
    "weasyprint>=62.0" \
    "mcp>=2.0.0" \
    "starlette>=0.40.0" \
    "uvicorn>=0.30.0" \
    "httpx>=0.27.0" \
    "psycopg[binary]>=3.2.0"

COPY scripts ./scripts
COPY plugin.json mcp_config.json config.example.json ./

RUN mkdir -p /data

EXPOSE 8766

CMD ["python3", "-m", "scripts.mcp_server.app"]
