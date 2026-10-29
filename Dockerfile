FROM ghcr.io/astral-sh/uv:0.10.11 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 crew \
    && useradd --uid 10001 --gid crew --no-create-home crew \
    && mkdir /app/data && chown crew:crew /app/data
USER crew
EXPOSE 8000
CMD ["uvicorn", "switchcrew.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
