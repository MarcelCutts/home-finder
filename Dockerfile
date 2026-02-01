FROM python:3.11-slim

# Install uv (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

# Configure uv for production
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies only (not the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY src/ ./src/

# Install the project
RUN uv sync --frozen --no-dev

# Create data directory for SQLite (will be mounted as volume)
RUN mkdir -p /app/data

# Run the scraper
CMD ["uv", "run", "home-finder"]
