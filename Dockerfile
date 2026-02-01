# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install uv (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

# Configure uv for production
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (better layer caching)
# Dependencies change less often than source code
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --frozen --no-dev --no-install-project

# Copy source code and install the project
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Create data directory for SQLite (will be mounted as volume)
RUN mkdir -p /app/data

# Run the scraper
CMD ["uv", "run", "home-finder"]
