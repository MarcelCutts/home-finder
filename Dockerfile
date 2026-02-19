FROM python:3.11-slim-bookworm

# Install uv (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /uvx /bin/

# Configure uv for production
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies (cache uv downloads across rebuilds)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-dev --no-install-project --no-editable

# Copy source code and install the project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-dev --no-editable

# Create data directory for SQLite (will be mounted as volume)
RUN mkdir -p /app/data

# Run the scraper
CMD ["uv", "run", "home-finder"]
