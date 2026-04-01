FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src/ src/
RUN uv pip install --system ".[dev]" && \
    uv pip install --system fastapi uvicorn jinja2

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import json, time; d=json.load(open('/root/.aurel2-crypto/heartbeat.json')); exit(0 if time.time()-d['timestamp']<300 else 1)" || exit 1

ENTRYPOINT ["aurel2-crypto"]
