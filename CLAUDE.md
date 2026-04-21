# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI-driven code review tool that takes a GitHub PR URL, runs multi-faceted review using Claude Code CLI in headless mode, and posts results back to the PR. Integrates with [code_graph_search](https://github.com/geekychris/code_graph_search) for deep cross-file static analysis.

## Build & Run

```bash
# Install locally (editable)
pip install -e .

# Run CLI
review-tool --help
review-tool review <pr-url> --dry-run
review-tool skills list
review-tool config init

# Docker
docker compose build
docker compose run review-tool review-tool review <pr-url> -c /config/review_tool.json
```

## Architecture

The review pipeline (`pipeline.py`) orchestrates: fetch PR via `gh` CLI → checkout repo → index with code_graph_search → run each skill as a separate Claude Code headless invocation → aggregate findings → post review to GitHub.

**Key modules:**
- `pipeline.py` — orchestrator tying all stages together
- `claude.py` — wrapper around `claude -p` (headless mode), handles prompt/output/flags
- `github.py` — wrapper around `gh` CLI for fetching PR data and posting reviews
- `graph_client.py` — httpx client for code_graph_search REST API
- `graph_lifecycle.py` — starts/stops code_graph_search Java subprocess, generates configs
- `prompt_builder.py` — assembles system + review prompts from skill + context + guidance
- `formatter.py` — converts findings to GitHub markdown body + inline comments
- `config.py` — Pydantic models with `${ENV_VAR}` interpolation, loaded from JSON

**Skills system** (`skills/`):
- `BaseSkill` ABC in `skills/__init__.py` defines the extension contract
- Each skill provides: `name`, `system_prompt(verbosity)`, `build_review_prompt(context)`, optional `pre_analyze(context)` for graph queries
- Built-in skills: `defects`, `security`, `quality`, `performance`
- Custom skills: drop a .py file in `custom_skills_dir`, subclass `BaseSkill`, call `SkillRegistry.register()`
- Each skill runs as an independent Claude Code invocation with its own focused system prompt

**Verbosity levels** control prompt detail: 0=summary only, 1=normal, 2=detailed with full file analysis, 3=debug logging.

## External Dependencies

- **Claude Code CLI** (`claude`) — must be installed and authenticated
- **gh CLI** — must be installed; auth via `GH_TOKEN` env var or `gh auth login`
- **code_graph_search** — Java 21 app; either run externally or set `graph.jar_path` in config for auto-management

## Docker Auth (Max Pro)

Mount `~/.claude` into the container for OAuth credential pass-through. The `docker-compose.yml` is pre-configured for this. For API key auth, set `ANTHROPIC_API_KEY` instead.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## Config

Config is JSON with `${ENV_VAR}` interpolation. See `config.example.json`. Key settings:
- `github.token` — defaults to `${GH_TOKEN}`
- `claude.model`, `claude.max_budget_usd` — control cost
- `graph.enabled`, `graph.mcp_mode` — code_graph_search integration mode
- `skills.enabled` — which skills to run
- `skills.custom_skills_dir` — directory for custom skill .py files
