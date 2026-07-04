# syntax=docker/dockerfile:1

# Base image and package index are overridable so the image can be built either
# from public registries (defaults) or through an internal mirror such as Nexus.
#   BASE_IMAGE     e.g. nexus.example.com:8082/python:3.12-slim-bookworm
#   PIP_INDEX_URL  e.g. https://nexus.example.com/repository/pypi-proxy/simple
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_SYSTEM_CERTS=true \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_INDEX_URL=${PIP_INDEX_URL} \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /code

# Trust any corporate CA certificates placed in certs/ (needed for TLS-intercepting
# proxies). The directory is committed with a .gitkeep, so this is a no-op by default.
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates

# uv itself comes from the (proxied) Python package index.
RUN pip install --no-cache-dir uv

# Install third-party dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
# Re-resolve against the configured index (UV_INDEX_URL) so artifact URLs point at
# that index (e.g. Nexus) instead of the lock's pinned files.pythonhosted.org URLs.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv lock && \
    uv sync --frozen --no-dev --group app --no-install-project

# Copy the library and app, then install the project itself.
COPY src ./src
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group app

EXPOSE 8000

CMD ["uv", "run", "--frozen", "--no-dev", "--group", "app", \
     "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
