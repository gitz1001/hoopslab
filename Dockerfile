# Hoops Lab website image. The database is built outside the container (NBA.com blocks most
# cloud IP ranges), exported with `python -m nbastats export`, then baked in or mounted.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8050 \
    NBA_DATA_DIR=/data \
    NBA_DB_PATH=/data/nba_serve.db \
    NBA_READONLY=1

WORKDIR /srv
COPY requirements-serve.txt .
RUN pip install -r requirements-serve.txt

COPY nbastats ./nbastats
COPY app ./app

# Bake the exported database into the image. The repo carries it gzipped through Git LFS
# (python -m nbastats export --gz); unpack_db.py checks it is real data, not an LFS pointer.
COPY scripts/unpack_db.py /srv/scripts/unpack_db.py
COPY data/nba_serve.db.gz /data/nba_serve.db.gz
RUN python /srv/scripts/unpack_db.py /data/nba_serve.db.gz

RUN useradd --create-home --uid 10001 hoops && chown -R hoops /srv
USER hoops

EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8050\")}/healthz', timeout=4)"

# 2 workers x 4 threads is plenty for a small instance; each worker holds its own caches.
CMD gunicorn app.wsgi:app --bind 0.0.0.0:${PORT} --workers ${WEB_WORKERS:-2} --threads 4 \
    --timeout 120 --access-logfile -
