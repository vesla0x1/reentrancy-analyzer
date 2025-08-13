FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Foundry
RUN curl -L https://foundry.paradigm.xyz | bash
ENV PATH="/root/.foundry/bin:${PATH}"
RUN foundryup

# Install Solc
RUN curl -L https://github.com/ethereum/solidity/releases/download/v0.8.23/solc-static-linux -o /usr/local/bin/solc \
    && chmod +x /usr/local/bin/solc

# Set working directory
WORKDIR /app

# Copy and install Python requirements
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy application files
COPY backend/ ./backend/

# Create temp directory for file processing
RUN mkdir -p /app/temp

# Expose API port
EXPOSE 8000

# Run the API server
CMD ["python", "backend/api.py"]
