# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies and CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
# Using --trusted-host to bypass SSL issues (common with corporate proxies)
RUN pip install --no-cache-dir --user \
    --trusted-host pypi.org \
    --trusted-host pypi.python.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt


# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Create non-root user for security
RUN groupadd -r controller && useradd -r -g controller controller

# Copy installed packages from builder
COPY --from=builder /root/.local /home/controller/.local

# Make sure scripts in .local are usable
ENV PATH=/home/controller/.local/bin:$PATH

# Copy application code
COPY src/ ./src/
COPY run.py .

# Set ownership
RUN chown -R controller:controller /app

# Switch to non-root user
USER controller

# Set Python to run unbuffered (important for logging)
ENV PYTHONUNBUFFERED=1

# Default command
ENTRYPOINT ["python", "run.py"]

# Default arguments (can be overridden)
CMD ["--in-cluster"]
