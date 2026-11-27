# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY services/forecaster/requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_PATH="/app/ml/models/forecaster_bundle.joblib"

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY cloudpulse_core ./cloudpulse_core
COPY ml/__init__.py ml/train.py ./ml/
COPY services/__init__.py ./services/__init__.py
COPY services/forecaster ./services/forecaster

USER appuser
EXPOSE 8001

# --workers 1 keeps online ACI state consistent across requests.
CMD ["uvicorn", "services.forecaster.app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
