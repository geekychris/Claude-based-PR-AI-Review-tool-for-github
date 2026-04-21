#!/usr/bin/env bash
# review-docker.sh — Run review-tool via Docker
#
# Usage:
#   ./review-docker.sh <pr-url> [options]
#   ./review-docker.sh https://github.com/owner/repo/pull/123
#   ./review-docker.sh https://github.com/owner/repo/pull/123 --dry-run -v 2
#   ./review-docker.sh build    # build the Docker image
#   ./review-docker.sh setup    # build image + generate config
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="review-tool"
CONFIG_FILE="$SCRIPT_DIR/review_tool.json"
ENV_FILE="$SCRIPT_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[review-docker]${NC} $*"; }
warn() { echo -e "${YELLOW}[review-docker]${NC} $*"; }
err()  { echo -e "${RED}[review-docker]${NC} $*" >&2; }

# ── Build ────────────────────────────────────────────────────────────────────

do_build() {
    log "Building Docker image (this may take a few minutes on first run)..."
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
    log "Image built: $IMAGE_NAME"
}

# ── Setup ────────────────────────────────────────────────────────────────────

do_setup() {
    do_build

    # Generate config if missing
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "Generating default config..."
        docker run --rm "$IMAGE_NAME" review-tool config init --path /dev/stdout > "$CONFIG_FILE"
        # Patch for Docker paths
        local tmp
        tmp=$(python3 -c "
import json, sys
c = json.load(open('$CONFIG_FILE'))
c['repo_checkout_dir'] = '/repos'
c['graph']['jar_path'] = '/opt/code-graph-search/code-graph-search.jar'
json.dump(c, sys.stdout, indent=2)
print()
")
        echo "$tmp" > "$CONFIG_FILE"
        log "Config written to $CONFIG_FILE (paths adjusted for Docker)"
    fi

    # Create .env if missing
    if [[ ! -f "$ENV_FILE" ]]; then
        log "Creating .env file..."
        cat > "$ENV_FILE" <<'EOF'
# GitHub token — create at https://github.com/settings/tokens (needs repo scope)
GH_TOKEN=

# Uncomment to use API key instead of Max Pro OAuth credentials
# ANTHROPIC_API_KEY=
EOF
        warn "Edit $ENV_FILE and set your GH_TOKEN"
    fi

    log ""
    log "Setup complete! Next steps:"
    log "  1. Edit .env and set GH_TOKEN"
    log "  2. ./review-docker.sh <pr-url>"
}

# ── Run ──────────────────────────────────────────────────────────────────────

run_review() {
    # Load .env if it exists
    local env_args=()
    if [[ -f "$ENV_FILE" ]]; then
        while IFS= read -r line; do
            # Skip comments and empty lines
            [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
            env_args+=(-e "$line")
        done < "$ENV_FILE"
    fi

    # Also pass through from current environment if set
    [[ -n "${GH_TOKEN:-}" ]]          && env_args+=(-e "GH_TOKEN=$GH_TOKEN")
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && env_args+=(-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")

    # Mount points
    local volumes=(
        -v "$HOME/.claude:/root/.claude:ro"       # Claude Code OAuth creds (Max Pro)
    )
    if [[ -f "$CONFIG_FILE" ]]; then
        volumes+=(-v "$CONFIG_FILE:/config/review_tool.json:ro")
    fi

    docker run --rm -it \
        "${env_args[@]}" \
        "${volumes[@]}" \
        -v review_tool_repos:/repos \
        "$IMAGE_NAME" \
        review-tool review "$@" --config /config/review_tool.json
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [[ $# -eq 0 ]]; then
    echo "Usage: ./review-docker.sh <pr-url> [options]"
    echo "       ./review-docker.sh setup    # build image + generate config"
    echo "       ./review-docker.sh build    # rebuild Docker image"
    echo ""
    echo "Options are passed directly to review-tool. Examples:"
    echo "  --dry-run              Preview review without posting to GitHub"
    echo "  --verbosity, -v INT    0=summary, 1=normal, 2=detailed, 3=debug"
    echo "  --skills, -s TEXT      Comma-separated skills (defects,security,quality,performance)"
    echo "  --no-graph             Skip code_graph_search"
    echo "  --guidance, -g PATH    Extra review guidance markdown file"
    echo "  --output, -o PATH      Write review to file instead of posting"
    exit 1
fi

case "$1" in
    build)
        do_build
        ;;
    setup)
        do_setup
        ;;
    *)
        # Auto-build if image doesn't exist
        if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
            warn "Docker image not found, building..."
            do_build
            echo ""
        fi
        run_review "$@"
        ;;
esac
