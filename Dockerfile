FROM python:3.10-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RASA_TELEMETRY_ENABLED=false \
    HOME=/app \
    HF_HOME=/app/.cache \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
ARG TORCH_VERSION=2.12.1+cpu
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      "torch==${TORCH_VERSION}" && \
    python -m pip install --no-cache-dir -r /app/requirements.txt

COPY actions /app/actions
COPY data /app/data
COPY teams_app /app/teams_app
COPY scripts /app/scripts
COPY config.yml domain.yml endpoints.yml /app/
COPY models /app/models

RUN useradd --create-home --uid 10001 appuser && \
    mkdir -p /app/.cache /app/data/policies /app/artifacts/logs && \
    chown -R appuser:appuser /app
USER appuser

# Rasa (5005), actions (5055), and the standalone bot port (3978) stay internal.
# EXPOSE 5005 5055 3978
EXPOSE 8610

FROM base AS deployment
# The web process also exposes the Teams bot at POST /api/messages.
# The supervisor starts private Rasa + actions and the public FastAPI server.
CMD ["python", "scripts/run_deployment.py", "web"]
