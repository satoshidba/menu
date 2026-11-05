# syntax=docker/dockerfile:1
FROM python:3.12.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# Optional extra CA certificates (certs/*.crt), e.g. behind a TLS-inspecting proxy. Empty by default.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && update-ca-certificates && apt-get clean

# OCR (receipts), PDF text, and a PostgreSQL 16 client for pg_dump/pg_restore (from the PGDG repository).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tesseract-ocr tesseract-ocr-eng poppler-utils \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y gnupg && apt-get autoremove -y && apt-get clean

WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY backend/alembic.ini backend/pytest.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --system --uid 10001 --home /app larder && mkdir -p /data && chown larder /data && chmod +x /usr/local/bin/docker-entrypoint.sh
USER larder
ENV LARDER_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["web"]
