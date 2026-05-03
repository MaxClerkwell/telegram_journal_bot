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
cp /root/.ssh/* /tmp/.ssh/ 2>/dev/null || true
chmod 700 /tmp/.ssh
chmod 600 /tmp/.ssh/* 2>/dev/null || true
ssh-keyscan github.com >> /tmp/.ssh/known_hosts 2>/dev/null
# Find private key: prefer id_rsa, then id_ed25519, then id_ecdsa
for keyname in id_rsa id_ed25519 id_ecdsa; do
  if [ -f "/tmp/.ssh/${keyname}" ]; then
    SSH_KEY="/tmp/.ssh/${keyname}"
    break
  fi
done
if [ -z "${SSH_KEY}" ]; then
  echo "WARNING: No SSH private key found in /root/.ssh (looked for id_rsa, id_ed25519, id_ecdsa)"
else
  export GIT_SSH_COMMAND="ssh -i ${SSH_KEY} -F /tmp/.ssh/config -o UserKnownHostsFile=/tmp/.ssh/known_hosts -o StrictHostKeyChecking=no"
  echo "Using SSH key: ${SSH_KEY}"
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
