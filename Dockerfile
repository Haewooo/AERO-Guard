FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd -r aeroguard && useradd -r -g aeroguard aeroguard

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

RUN mkdir -p /data && chown aeroguard:aeroguard /data
VOLUME ["/data"]

ENV AEROGUARD_DB_PATH=/data/audit.db \
    AEROGUARD_HOST=0.0.0.0 \
    AEROGUARD_PORT=8000

USER aeroguard
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
