#!/usr/bin/env bash
# review.sh — Run review-tool natively on macOS
#
# Usage:
#   ./review.sh <pr-url> [options]
#   ./review.sh https://github.com/owner/repo/pull/123
#   ./review.sh https://github.com/owner/repo/pull/123 --dry-run -v 2
#   ./review.sh https://github.com/owner/repo/pull/123 --skills security,defects
#   ./review.sh setup    # first-time setup
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
CONFIG_FILE="$SCRIPT_DIR/review_tool.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[review]${NC} $*"; }
warn() { echo -e "${YELLOW}[review]${NC} $*"; }
err()  { echo -e "${RED}[review]${NC} $*" >&2; }

# ── Setup ────────────────────────────────────────────────────────────────────

do_setup() {
    log "Setting up review-tool..."

    # Check prerequisites
    local missing=()
    command -v python3 >/dev/null || missing+=("python3")
    command -v gh >/dev/null      || missing+=("gh (brew install gh)")
    command -v claude >/dev/null  || missing+=("claude (npm install -g @anthropic-ai/claude-code)")

    if [[ ${#missing[@]} -gt 0 ]]; then
        err "Missing required tools:"
        for tool in "${missing[@]}"; do
            err "  - $tool"
        done
        exit 1
    fi

    # Check gh auth
    if ! gh auth status &>/dev/null; then
        warn "gh CLI is not authenticated. Running 'gh auth login'..."
        gh auth login
    fi

    # Create venv and install
    if [[ ! -d "$VENV_DIR" ]]; then
        log "Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    log "Installing review-tool..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$SCRIPT_DIR"

    # Generate config if missing
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "Generating default config at $CONFIG_FILE"
        "$VENV_DIR/bin/review-tool" config init --path "$CONFIG_FILE"
    fi

    log "Setup complete!"
    log ""
    log "Usage:"
    log "  ./review.sh <pr-url>              # review a PR"
    log "  ./review.sh <pr-url> --dry-run    # preview without posting"
    log "  ./review.sh <pr-url> -v 2         # detailed review"
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [[ $# -eq 0 ]]; then
    echo "Usage: ./review.sh <pr-url> [options]"
    echo "       ./review.sh setup"
    echo ""
    echo "Options are passed directly to review-tool. Examples:"
    echo "  --dry-run              Preview review without posting to GitHub"
    echo "  --verbosity, -v INT    0=summary, 1=normal, 2=detailed, 3=debug"
    echo "  --skills, -s TEXT      Comma-separated skills (defects,security,quality,performance)"
    echo "  --model, -m TEXT       Override Claude model"
    echo "  --max-budget FLOAT     Max USD per skill"
    echo "  --no-graph             Skip code_graph_search"
    echo "  --guidance, -g PATH    Extra review guidance markdown file"
    echo "  --output, -o PATH      Write review to file instead of posting"
    exit 1
fi

if [[ "$1" == "setup" ]]; then
    do_setup
    exit 0
fi

# Auto-setup if venv doesn't exist
if [[ ! -d "$VENV_DIR" ]]; then
    warn "First run detected, running setup..."
    do_setup
    echo ""
fi

# Use config file if it exists
CONFIG_ARGS=()
if [[ -f "$CONFIG_FILE" ]]; then
    CONFIG_ARGS=(--config "$CONFIG_FILE")
fi

exec "$VENV_DIR/bin/review-tool" review "$@" "${CONFIG_ARGS[@]}"
