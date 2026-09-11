FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync

COPY . .

CMD ["uv", "run", "python", "main.py"]
