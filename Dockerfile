FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    nodejs \
    npm \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode CLI (available for manual use)
RUN npm install -g opencode-ai

# Set working directory
WORKDIR /app

# Copy and install Python dependencies
COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot/ /app/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
