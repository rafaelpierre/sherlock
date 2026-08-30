FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 app \
    && chown -R app:app /app

USER app

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=2)"]

CMD ["backend-api"]
