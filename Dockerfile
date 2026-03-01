# Finviz MCP Server Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY src/ ./src/
COPY pyproject.toml .
COPY run_server.py .

# Install the package in editable mode
RUN pip install --no-cache-dir -e .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO
ENV RATE_LIMIT_REQUESTS_PER_MINUTE=100

# Expose port for SSE transport
EXPOSE 8000

# Default command - use the installed entry point
CMD ["finviz-mcp-server"]
