FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY sns ./sns
RUN uv sync --frozen --no-dev
CMD ["uv", "run", "python", "-c", "import sns; print('sns ok')"]
