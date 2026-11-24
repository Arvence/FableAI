FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY api/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade "pip>=26.2" \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY api/app /app/api/app
COPY api/migrations /app/api/migrations
COPY api/alembic.ini /app/api/alembic.ini
COPY scripts/container_start.py /app/container_start.py

WORKDIR /app/api
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read(); print('ok')" || exit 1

CMD ["python", "/app/container_start.py"]
