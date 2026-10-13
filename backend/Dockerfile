FROM python:3.11-slim

# Install system dependencies for PDF processing
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    poppler-utils \
    tesseract-ocr \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv: static binary from the official image, pinned for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

# The virtualenv lives OUTSIDE /app: docker-compose bind-mounts ./backend over /app in dev,
# which would otherwise hide a /app/.venv created at build time.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install dependencies first (cached until pyproject.toml / uv.lock change).
# Runtime deps only: tests, lint and type checks run on the host / in CI via `uv run`.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
