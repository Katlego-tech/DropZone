# Dependencies are resolved in a builder stage so the runtime image carries no
# build tooling, and the layers are ordered so that editing source does not
# invalidate the dependency install.
FROM python:3.14.6-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
RUN uv sync --locked --no-dev


FROM python:3.14.6-slim AS runtime

# Unprivileged, and no login shell. A container that only ever serves HTTP has
# no business running as root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin dropzone

WORKDIR /app

COPY --from=builder --chown=dropzone:dropzone /app/.venv /app/.venv
COPY --from=builder --chown=dropzone:dropzone /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER dropzone
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"]

CMD ["uvicorn", "dropzone.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
