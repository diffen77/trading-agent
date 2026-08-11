FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2,<27" \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --home-dir /app \
        --no-create-home --shell /usr/sbin/nologin agent

COPY --chown=agent:agent . .
RUN mkdir -p /app/data && chown agent:agent /app/data

USER agent

HEALTHCHECK --interval=15s --timeout=10s --start-period=15s --retries=5 \
    CMD ["python", "-m", "src.healthcheck", "readiness"]

CMD ["python", "-m", "src.main"]
