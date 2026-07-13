# python 3.12: mediapipe (webcam pose extraction) has no 3.13 wheels yet
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgl1/libglib2.0-0: runtime libs for opencv (mediapipe dependency)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r aeroguard && useradd -r -g aeroguard aeroguard

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "mediapipe>=0.10" "faster-whisper>=1.0" "piper-tts>=1.4"

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

RUN mkdir -p /data && chown aeroguard:aeroguard /data
VOLUME ["/data"]

ENV AEROGUARD_DB_PATH=/data/audit.db \
    AEROGUARD_HOST=0.0.0.0 \
    AEROGUARD_PORT=8000 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HF_HUB_OFFLINE=1

USER aeroguard
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
