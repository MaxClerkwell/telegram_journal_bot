#!/bin/bash
set -e
set -o pipefail

# Configure OpenCode
mkdir -p /root/.config/opencode

PROVIDER="${LLM_PROVIDER:-google}"
MODEL="${LLM_MODEL:-gemini-2.0-flash}"

# Build provider-specific API key block
case "$PROVIDER" in
  google)
    API_KEY="${GOOGLE_API_KEY:-}"
    PROVIDER_JSON="\"google\": {\"apiKey\": \"${API_KEY}\"}"
    ;;
  anthropic)
    API_KEY="${ANTHROPIC_API_KEY:-}"
    PROVIDER_JSON="\"anthropic\": {\"apiKey\": \"${API_KEY}\"}"
    ;;
  openai)
    API_KEY="${OPENAI_API_KEY:-}"
    PROVIDER_JSON="\"openai\": {\"apiKey\": \"${API_KEY}\"}"
    ;;
  *)
    echo "Unknown LLM_PROVIDER: $PROVIDER"
    PROVIDER_JSON=""
    ;;
esac

cat > /root/.config/opencode/config.json <<EOF
{
  "model": "${PROVIDER}/${MODEL}",
  "providers": {
    ${PROVIDER_JSON}
  }
}
EOF

echo "OpenCode config written for provider=${PROVIDER} model=${MODEL}"

# Fix git safe directory for mounted volume
git config --global --add safe.directory /repo

# Copy read-only SSH mount to writable location and add GitHub host key
mkdir -p /tmp/.ssh
chmod 700 /tmp/.ssh
ssh-keyscan github.com >> /tmp/.ssh/known_hosts 2>/dev/null
if [ -f /tmp/.ssh/id_private ]; then
  cp /tmp/.ssh/id_private /tmp/.ssh/id_private_rw
  chown root:root /tmp/.ssh/id_private_rw
  chmod 600 /tmp/.ssh/id_private_rw
  export GIT_SSH_COMMAND="ssh -i /tmp/.ssh/id_private_rw -F /dev/null -o UserKnownHostsFile=/tmp/.ssh/known_hosts -o StrictHostKeyChecking=no"
  echo "Using SSH key: /tmp/.ssh/id_private"
else
  echo "WARNING: No SSH private key found at /tmp/.ssh/id_private. Set SSH_PRIVATE_KEY_PATH in .env."
  export GIT_SSH_COMMAND="ssh -F /dev/null -o UserKnownHostsFile=/tmp/.ssh/known_hosts -o StrictHostKeyChecking=no"
fi

# Configure git
GIT_NAME="${GIT_USER_NAME:-Journal Bot}"
GIT_EMAIL="${GIT_USER_EMAIL:-journal@bot.local}"

git config --global user.name "${GIT_NAME}"
git config --global user.email "${GIT_EMAIL}"

# Clone journal repo if not present
if [ -n "${JOURNAL_REPO}" ] && [ ! -d /repo/.git ]; then
  echo "Cloning journal repo: ${JOURNAL_REPO}"
  git clone "${JOURNAL_REPO}" /repo
fi

# Ensure repo is on main branch and up to date
if [ -d /repo/.git ]; then
  cd /repo
  git checkout main 2>/dev/null || git checkout -b main
  git pull origin main 2>/dev/null || true
  cd /app
else
  echo "WARNING: /repo is not a git repository. Set JOURNAL_REPO in .env or mount a cloned repo."
fi

echo "Starting journal bot..."
exec python bot.py
