FROM python:3.14-slim

LABEL maintainer="opensculpt" \
      description="OpenSculpt — The Self-Evolving Agentic OS" \
      version="0.1.0"

# Prevent Python from writing .pyc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (so the OS can manage containers when it evolves that capability)
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e "." 2>/dev/null || true

# Install Playwright for browser tool evolution
RUN pip install --no-cache-dir playwright 2>/dev/null && \
    python -m playwright install chromium --with-deps 2>/dev/null || true

# Copy full source
COPY . .

# Install the package
RUN pip install --no-cache-dir -e "."

# Initialize workspace
RUN python -c "from pathlib import Path; Path('.opensculpt').mkdir(exist_ok=True); Path('.opensculpt/agents').mkdir(exist_ok=True)"

# Dashboard port
EXPOSE 8420

# Launch the OS
CMD ["python", "-m", "agos.serve"]
