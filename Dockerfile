# Dockerfile for the webcrawl-mcp server.
#
# Runs the FastMCP-based stdio server (`python -m webcrawl_mcp`). Lightweight
# image (~150 MB) — pure-Python deps, no ML wheels.
#
# Used by Glama's MCP listing checks (https://glama.ai/mcp/servers): the
# server must start and respond to MCP introspection (tools/list).
#
# Usage:
#   docker run -i --rm ghcr.io/andyliszewski/webcrawl-mcp
#
# Optional Firecrawl fallback for transport-blocked pages:
#   docker run -i --rm -e FIRECRAWL_API_KEY=... ghcr.io/andyliszewski/webcrawl-mcp

FROM python:3.13-slim

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install .

CMD ["python", "-m", "webcrawl_mcp"]
