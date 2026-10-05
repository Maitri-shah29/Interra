FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY docs ./docs
COPY Dockerfile ./Dockerfile
COPY vendor ./vendor
COPY interra_submission.py submission.yaml ./
COPY scripts ./scripts
COPY kaggle ./kaggle

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir '.[voice]' \
    && useradd --create-home --uid 10001 interra \
    && mkdir -p /app/artifacts/traces \
    && chown -R interra:interra /app

USER interra

CMD ["python", "-m", "agent.fdb_livekit", "start"]
