#!/bin/bash
set -e

# Copy SSH keys from ~/.ssh into ./ssh with correct permissions
echo "Copying SSH keys..."
mkdir -p ./ssh
cp ~/.ssh/* ./ssh/ 2>/dev/null || true
chmod 700 ./ssh
chmod 600 ./ssh/* 2>/dev/null || true
echo "SSH keys copied."

# Create .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env created from .env.example — please fill in your values:"
  echo "  vim .env"
  exit 0
fi

echo "Run 'docker compose up -d' to start."
