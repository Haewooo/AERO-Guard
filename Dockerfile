# python 3.12: mediapipe (webcam pose extraction) has no 3.13 wheels yet.
# Pinned by digest, not by tag: a floating tag means two builds of the same
# commit can produce different images. Refresh with:
#   docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
FROM python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgl1/libglib2.0-0: runtime libs for opencv (mediapipe dependency)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r aeroguard && useradd -r -g aeroguard aeroguard

WORKDIR /app

# Every dependency, including the heavy optional ones, is installed from a
# hash-pinned lock: the build fails rather than silently accepting a wheel
# that does not match the recorded SHA-256. The lock is per architecture
# because some wheels (mediapipe) are published at different versions for
# x86_64 and aarch64. Regenerate with ./scripts/lock-requirements.sh.
ARG TARGETARCH
COPY requirements-full-x86_64.lock requirements-full-aarch64.lock ./
RUN case "${TARGETARCH:-amd64}" in \
      amd64) LOCK=requirements-full-x86_64.lock ;; \
      arm64) LOCK=requirements-full-aarch64.lock ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && pip install --no-cache-dir --require-hashes -r "$LOCK"

# Pre-fetch Whisper weights at build time so the deployed container never
# needs network access (read-only fs + HF_HUB_OFFLINE below). The piper TTS
# voice is committed in-repo (models/) because the HF large-file CDN rejects
# unauthenticated downloads intermittently — COPY keeps the build air-gap safe.
ENV HF_HOME=/app/models
COPY models/en_US-amy-medium.onnx models/en_US-amy-medium.onnx.json /app/models/
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')" \
    && chmod -R a+rX /app/models

COPY backend ./backend
COPY frontend ./frontend

# /data holds the audit database; /anchors holds the chain-head anchor log.
# They are separate volumes so a rewrite of the database is still
# contradicted by the anchors.
RUN mkdir -p /data /anchors && chown aeroguard:aeroguard /data /anchors
VOLUME ["/data", "/anchors"]

# The audit HMAC key defaults to audit.key beside the database (/data);
# override AEROGUARD_AUDIT_KEY from a secret store in production.
ENV AEROGUARD_DB_PATH=/data/audit.db \
    AEROGUARD_AUDIT_ANCHOR_PATH=/anchors/audit-anchors.log \
    AEROGUARD_HOST=0.0.0.0 \
    AEROGUARD_PORT=8000 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HF_HUB_OFFLINE=1

USER aeroguard
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
