# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /code

# Install third-party dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group app --no-install-project

# Copy the library and app, then install the project itself.
COPY src ./src
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group app

EXPOSE 8000

CMD ["uv", "run", "--frozen", "--no-dev", "--group", "app", \
     "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
