#!/usr/bin/env bash
# Setup script for cadgenbench mcp-agent on a DigitalOcean Ubuntu 24.04 droplet.
# Run as root after SSH-ing in:
#   bash setup_do.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Config — edit these or set them as env vars before running
# ---------------------------------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/pzfreo/cadgenbench}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/cadgenbench}"

# ---------------------------------------------------------------------------
# Prompt for secrets if not already set
# ---------------------------------------------------------------------------
if [[ -z "${HF_TOKEN:-}" ]]; then
    read -rsp "HuggingFace token (HF_TOKEN): " HF_TOKEN
    echo
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]] && [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "Set at least one LLM API key:"
    read -rsp "  ANTHROPIC_API_KEY (or press Enter to skip): " ANTHROPIC_API_KEY
    echo
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        read -rsp "  OPENAI_API_KEY: " OPENAI_API_KEY
        echo
    fi
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
echo "==> Installing system packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    git \
    xvfb \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    curl \
    ca-certificates

# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env"
fi
export PATH="$HOME/.local/bin:$PATH"
echo "==> uv $(uv --version)"

# ---------------------------------------------------------------------------
# Clone repo
# ---------------------------------------------------------------------------
echo "==> Cloning $REPO_URL ($BRANCH) → $INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ---------------------------------------------------------------------------
# Install cadgenbench (editable, with dev deps for eval)
# ---------------------------------------------------------------------------
echo "==> Installing cadgenbench…"
uv sync --extra mcp-agent

# ---------------------------------------------------------------------------
# Pre-warm build123d-mcp (downloads OCP/build123d under Python 3.12)
# This takes a few minutes on first run.
# ---------------------------------------------------------------------------
echo "==> Pre-warming build123d-mcp (Python 3.12) — this may take 5–10 min…"
uvx --python 3.12 build123d-mcp@latest --help >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Write env file
# ---------------------------------------------------------------------------
ENV_FILE="$INSTALL_DIR/.env"
cat > "$ENV_FILE" <<EOF
export HF_TOKEN="${HF_TOKEN}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export CADGENBENCH_DATA_REPO=HuggingAI4Engineering/cadgenbench-data
export CADGENBENCH_DATA_GT_REPO=HuggingAI4Engineering/cadgenbench-data-gt
export PATH="\$HOME/.local/bin:\$PATH"
EOF
chmod 600 "$ENV_FILE"
echo "==> Secrets written to $ENV_FILE (chmod 600)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
cat <<EOF

==> Setup complete. To run:

    cd $INSTALL_DIR
    source .env
    uv run cadgenbench mcp-agent run 101 \\
      --mcp-server uvx \\
      --mcp-args --python 3.12 build123d-mcp@latest

    # Or run all fixtures in parallel:
    uv run cadgenbench mcp-agent run --all --parallel 4 \\
      --mcp-server uvx \\
      --mcp-args --python 3.12 build123d-mcp@latest

EOF
