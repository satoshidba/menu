# syntax=docker/dockerfile:1
FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt
# Optional extra CA certificates (certs/*.crt), e.g. behind a TLS-inspecting proxy. Empty by default.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && update-ca-certificates && apt-get clean
WORKDIR /app
COPY mcp-server/pyproject.toml ./
COPY mcp-server/larder_mcp ./larder_mcp
RUN pip install .
RUN useradd --system --uid 10002 larder-mcp
USER larder-mcp
EXPOSE 8001
ENV LARDER_API_URL=http://web:8000
CMD ["larder-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8001"]
