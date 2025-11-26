FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for git or build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY README.md .
COPY src/ src/
COPY main.py .
COPY scripts/ scripts/

# Install dependencies
# We use pip to install the current directory in editable mode or just the dependencies
RUN pip install --no-cache-dir .

# Command to run the bot
CMD ["python", "main.py"]
