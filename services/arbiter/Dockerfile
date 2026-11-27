# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY services/arbiter/requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY cloudpulse_core ./cloudpulse_core
COPY deploy ./deploy
COPY services/__init__.py ./services/__init__.py
COPY services/collector/__init__.py ./services/collector/__init__.py
COPY services/collector/promql.py ./services/collector/promql.py
COPY services/arbiter ./services/arbiter
RUN mkdir -p /app/experiments/results && chown -R appuser:appuser /app/experiments

USER appuser
EXPOSE 8002

ENTRYPOINT ["python", "-m", "services.arbiter.loop"]
CMD ["--help"]
