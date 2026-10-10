# One runtime for CLI and dashboard. uv's pinned Python build includes the WAL fix;
# the official Python/bookworm image still links Debian's older SQLite 3.40.1.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS runtime-base
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed
RUN uv python install 3.14.7
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --python 3.14.7
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --python 3.14.7
# Runtime images never install packages. Omit base pip and ensurepip, including
# their older vendored setuptools/msgpack; package installation stays in builder.
RUN rm -rf /opt/python/*/lib/python3.14/ensurepip \
    /opt/python/*/lib/python3.14/site-packages/pip \
    /opt/python/*/lib/python3.14/site-packages/pip-*.dist-info \
    /opt/python/*/bin/pip*

FROM runtime-base AS runtime
RUN useradd --create-home --uid 1000 --shell /bin/bash appuser \
    && mkdir -p /app/data && chown appuser:appuser /app/data
WORKDIR /app
COPY --from=builder /opt/python /opt/python
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
RUN python -c 'import sys, sqlite3; assert sys.version_info[:3] == (3, 14, 7); assert sqlite3.sqlite_version_info >= (3, 51, 3); print(sys.version, "SQLite", sqlite3.sqlite_version)'

FROM runtime AS collector
CMD ["home-finder"]

FROM runtime AS dashboard
EXPOSE 8000
CMD ["home-finder", "--serve", "--no-pipeline"]
