# syntax=docker/dockerfile:1.6

# ─────────────────────────────────────────────── builder ───
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-time deps only (kept out of the runtime image).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install deps into an isolated venv we can copy into the runtime stage.
COPY pyproject.toml ./
COPY pipeline ./pipeline
COPY server ./server

RUN python -m venv /venv \
 && /venv/bin/pip install --upgrade pip setuptools wheel

# Install CPU-only torch first from the official PyTorch index, otherwise
# pip pulls 2+ GB of unused CUDA libraries on aarch64. The subsequent
# `pip install .` sees torch already satisfied and skips the CUDA-bundled
# wheel.
RUN /venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu \
      torch

RUN /venv/bin/pip install .


# ─────────────────────────────────────────────── runtime ───
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/venv/bin:$PATH \
    PYTHONPATH=/app \
    HOST=0.0.0.0 \
    PORT=8000

# ffmpeg is required by the stitch + mock-i2v stages. tini gives us proper
# signal handling so `docker stop` exits cleanly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*

# Copy the prebuilt venv (contains all Python deps).
COPY --from=builder /venv /venv

WORKDIR /app

# App source. Runs/ is created as a volume mount target.
COPY pyproject.toml /app/
COPY pipeline /app/pipeline
COPY server /app/server

RUN mkdir -p /app/runs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request, sys; \
    r = urllib.request.urlopen('http://127.0.0.1:' + '${PORT}' + '/healthz', timeout=3); \
    sys.exit(0 if r.status == 200 else 1)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "server.main"]
