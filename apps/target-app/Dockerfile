# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — builder: resolve dependencies into a self-contained virtualenv.
# Kept separate so pip, build caches and compilers never reach the final image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Copied alone so this layer is cached and only re-runs when deps change,
# not on every edit to application code.
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime: the image that actually ships.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root. A container that scales to 15 replicas should not run as root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

USER appuser
EXPOSE 8000

# --workers 1 is deliberate: one pod == one unit of capacity. Multiple workers
# per pod would make the measured constant C ambiguous (CLAUDE.md §3.2).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
