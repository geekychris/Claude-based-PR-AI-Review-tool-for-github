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

# code_graph_search — look in sibling directory or configurable location
CGS_DIR="${CODE_GRAPH_SEARCH_DIR:-$SCRIPT_DIR/../code_graph_search}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[review]${NC} $*"; }
warn() { echo -e "${YELLOW}[review]${NC} $*"; }
err()  { echo -e "${RED}[review]${NC} $*" >&2; }
info() { echo -e "${CYAN}[review]${NC} $*"; }

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

    # Check optional: Java for code_graph_search
    if command -v java >/dev/null; then
        local java_version
        java_version=$(java -version 2>&1 | head -1)
        log "Java found: $java_version"
    else
        warn "Java not found — code_graph_search will not be available"
        warn "  Install: brew install openjdk@21"
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

    # Locate code_graph_search
    local cgs_resolved=""
    if [[ -d "$CGS_DIR" && -f "$CGS_DIR/pom.xml" ]]; then
        cgs_resolved="$(cd "$CGS_DIR" && pwd)"
        log "Found code_graph_search at $cgs_resolved"

        # Build JAR if not present
        if [[ ! -f "$cgs_resolved/app/target/code-graph-search.jar" ]]; then
            if command -v java >/dev/null && command -v mvn >/dev/null; then
                log "Building code_graph_search JAR..."
                (cd "$cgs_resolved" && bash build.sh) || warn "Build failed — you can build manually later"
            elif command -v java >/dev/null; then
                warn "Maven not found — install with: brew install maven"
                warn "Then build: cd $cgs_resolved && ./build.sh"
            fi
        else
            log "code_graph_search JAR already built"
        fi
    else
        warn "code_graph_search not found at $CGS_DIR"
        warn "  Clone it: git clone https://github.com/geekychris/code_graph_search.git $CGS_DIR"
        warn "  Or set CODE_GRAPH_SEARCH_DIR=/path/to/code_graph_search"
    fi

    # Generate config with code_graph_search paths
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "Generating config at $CONFIG_FILE"
        "$VENV_DIR/bin/review-tool" config init --path "$CONFIG_FILE"

        # Patch config with code_graph_search paths if available
        if [[ -n "$cgs_resolved" ]]; then
            local jar_path="$cgs_resolved/app/target/code-graph-search.jar"
            python3 -c "
import json, sys
c = json.load(open('$CONFIG_FILE'))
c['graph']['code_graph_search_dir'] = '$cgs_resolved'
c['graph']['auto_start'] = True
if __import__('os').path.exists('$jar_path'):
    c['graph']['jar_path'] = '$jar_path'
json.dump(c, open('$CONFIG_FILE', 'w'), indent=2)
print()
"
            log "Config updated with code_graph_search paths"
        fi
    fi

    log ""
    log "Setup complete!"
    log ""
    log "Usage:"
    log "  ./review.sh <pr-url>              # review a PR"
    log "  ./review.sh <pr-url> --dry-run    # preview without posting"
    log "  ./review.sh <pr-url> -v 2         # detailed review"
    if [[ -n "$cgs_resolved" ]]; then
        log ""
        log "code_graph_search will auto-start for each review."
        log "The PR branch will be checked out and indexed before analysis."
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [[ $# -eq 0 ]]; then
    echo "Usage: ./review.sh <pr-url> [options]"
    echo "       ./review.sh setup"
    echo ""
    echo "Options are passed directly to review-tool. Examples:"
    echo "  --dry-run              Preview review without posting to GitHub"
    echo "  --verbosity, -v INT    0=summary, 1=normal, 2=detailed, 3=debug"
    echo "  --skills, -s TEXT      Comma-separated skills (defects,security,quality,performance,java,rust,go,typescript)"
    echo "  --model, -m TEXT       Override Claude model"
    echo "  --max-budget FLOAT     Max USD per skill"
    echo "  --no-graph             Skip code_graph_search"
    echo "  --guidance, -g PATH    Extra review guidance markdown file"
    echo "  --output, -o PATH      Write review to file instead of posting"
    echo ""
    echo "Environment:"
    echo "  CODE_GRAPH_SEARCH_DIR  Path to code_graph_search source (default: ../code_graph_search)"
    echo "  GH_TOKEN               GitHub personal access token"
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
