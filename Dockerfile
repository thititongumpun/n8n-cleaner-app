# Build stage
FROM python:3.14-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential pkg-config libssl-dev \
    libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy and build dependencies
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# Runtime stage
FROM python:3.14-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application
WORKDIR /app
COPY . .

# Run the application
CMD ["/app/.venv/bin/fastapi", "run", "main.py", "--port", "80", "--host", "0.0.0.0"]