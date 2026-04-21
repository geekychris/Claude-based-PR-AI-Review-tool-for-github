# review-tool

AI-driven code review tool that analyzes GitHub pull requests using [Claude Code](https://claude.ai/code) in headless mode. It runs multiple specialized review passes (defects, security, quality, performance), optionally enriched with deep static analysis from [code_graph_search](https://github.com/geekychris/code_graph_search), and posts structured findings back to the PR.

## Features

- **Multi-skill reviews** — each skill (defects, security, quality, performance) runs as an independent Claude Code session with a focused system prompt, producing higher quality findings than a single monolithic review
- **Deep code analysis** — integrates with code_graph_search to provide call graphs, type hierarchies, and cross-file dependency information as context for the review
- **Tunable verbosity** — four levels from quick summary to exhaustive analysis with data flow tracing
- **Extensible** — add custom review skills by dropping a Python file in a directory
- **CI-friendly** — exits with code 1 when critical/high findings are present; supports `--dry-run` and file output
- **GitHub native** — posts reviews with inline comments on specific diff lines, automatically chooses APPROVE / COMMENT / REQUEST_CHANGES

---

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
  - [Native (macOS / Linux)](#native-macos--linux)
  - [Docker](#docker)
- [Authentication](#authentication)
- [Configuration](#configuration)
- [Usage](#usage)
  - [CLI Reference](#cli-reference)
  - [Verbosity Levels](#verbosity-levels)
  - [Custom Guidance](#custom-guidance)
- [Architecture](#architecture)
  - [Review Pipeline](#review-pipeline)
  - [Module Overview](#module-overview)
  - [Skills System](#skills-system)
  - [code_graph_search Integration](#code_graph_search-integration)
  - [Claude Code Integration](#claude-code-integration)
  - [Output Formatting](#output-formatting)
- [Extending review-tool](#extending-review-tool)
  - [Writing a Custom Skill](#writing-a-custom-skill)
  - [Skill API Reference](#skill-api-reference)
  - [Custom Guidance Files](#custom-guidance-files)
- [Docker Details](#docker-details)
- [Development](#development)

---

## Quick Start

```bash
# Clone and setup
git clone <this-repo>
cd review_tool
./review.sh setup

# Review a PR (dry-run first to see output)
./review.sh https://github.com/owner/repo/pull/123 --dry-run

# Post the review to GitHub
./review.sh https://github.com/owner/repo/pull/123
```

---

## Installation

### Native (macOS / Linux)

**Prerequisites:**

| Tool | Install |
|------|---------|
| Python 3.11+ | `brew install python` or system package manager |
| gh CLI | `brew install gh` |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` |
| Java 21 (optional) | `brew install openjdk@21` — only needed for code_graph_search |

**Install via shell script:**

```bash
./review.sh setup
```

This will:
1. Check all prerequisites are installed
2. Ensure `gh` is authenticated (prompts login if needed)
3. Create a Python virtual environment in `.venv/`
4. Install review-tool and its dependencies
5. Generate a default `review_tool.json` config file

**Install manually:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
review-tool config init
```

### Docker

```bash
./review-docker.sh setup
```

This will:
1. Build the Docker image (includes Java 21, Python, gh CLI, Claude Code CLI, code_graph_search)
2. Generate `review_tool.json` with Docker-appropriate paths
3. Create a `.env` template file for your tokens

Edit the generated `.env` file:
```
GH_TOKEN=ghp_your_token_here
```

---

## Authentication

### GitHub

The tool uses `gh` CLI for all GitHub operations. Choose one:

| Method | How |
|--------|-----|
| gh login (recommended) | `gh auth login` — stores in system keychain |
| Environment variable | `export GH_TOKEN=ghp_...` |
| Config file | Set `github.token` in `review_tool.json` |

The token needs **repo** scope to read PR data and post reviews. Create one at https://github.com/settings/tokens.

### Claude Code

| Environment | Auth Method |
|-------------|-------------|
| Native (Max Pro) | `claude auth login` — OAuth via browser |
| Native (API) | `export ANTHROPIC_API_KEY=sk-ant-...` |
| Docker (Max Pro) | Mount `~/.claude` into container (done automatically by `review-docker.sh`) |
| Docker (API) | Set `ANTHROPIC_API_KEY` in `.env` |

---

## Configuration

Configuration is a JSON file (default: `review_tool.json`) with environment variable interpolation. Generate a default:

```bash
review-tool config init
```

### Full Configuration Reference

```json
{
  "github": {
    "token": "${GH_TOKEN}"
  },
  "claude": {
    "model": "sonnet",
    "max_turns": 30,
    "max_budget_usd": 1.0,
    "allowed_tools": ["Bash", "Read", "Grep", "Glob"],
    "permission_mode": "bypassPermissions",
    "extra_system_prompt": ""
  },
  "graph": {
    "enabled": true,
    "host": "http://localhost:8080",
    "startup_timeout_seconds": 120,
    "jar_path": null,
    "config_path": null,
    "mcp_mode": false
  },
  "skills": {
    "enabled": ["defects", "security", "quality", "java", "rust", "go", "typescript"],
    "custom_skills_dir": null
  },
  "verbosity": 1,
  "repo_checkout_dir": "/tmp/review_tool_repos",
  "guidance_file": null
}
```

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `github` | `token` | `${GH_TOKEN}` | GitHub personal access token. Supports `${VAR}` and `${VAR:-default}` syntax. |
| `claude` | `model` | `"sonnet"` | Claude model to use (`sonnet`, `opus`, `haiku`). |
| | `max_turns` | `30` | Maximum agentic turns per skill invocation. |
| | `max_budget_usd` | `1.0` | Maximum spend per skill. |
| | `allowed_tools` | `["Bash","Read","Grep","Glob"]` | Tools Claude can use during review. |
| | `permission_mode` | `"bypassPermissions"` | Claude Code permission mode. |
| | `extra_system_prompt` | `""` | Appended to every skill's system prompt. |
| `graph` | `enabled` | `true` | Enable code_graph_search integration. |
| | `host` | `"http://localhost:8080"` | code_graph_search REST API URL. |
| | `startup_timeout_seconds` | `120` | Timeout waiting for code_graph_search to start. |
| | `jar_path` | `null` | Path to code_graph_search JAR. If set, review-tool manages the process lifecycle. If `null`, assumes it's running externally. |
| | `config_path` | `null` | Path to code_graph_search config YAML. Auto-generated if not set. |
| | `mcp_mode` | `false` | If `true`, pass code_graph_search as an MCP server to Claude (Claude can call graph tools directly). |
| `skills` | `enabled` | `["defects","security","quality"]` | Which skills to run by default. |
| | `custom_skills_dir` | `null` | Directory containing custom skill `.py` files. |
| | `verbosity` | `1` | Default verbosity level (0-3). |
| | `repo_checkout_dir` | `"/tmp/review_tool_repos"` | Where to clone PR repositories. |
| | `guidance_file` | `null` | Path to a markdown file with extra review instructions. |

### Environment Variable Interpolation

Any string value in the config can reference environment variables:

```json
{
  "github": { "token": "${GH_TOKEN}" },
  "claude": { "model": "${REVIEW_MODEL:-sonnet}" }
}
```

- `${VAR}` — replaced with the value of `VAR`, or empty string if unset
- `${VAR:-default}` — replaced with the value of `VAR`, or `default` if unset

---

## Usage

### Shell Scripts

```bash
# Native macOS/Linux
./review.sh <pr-url> [options]
./review.sh setup

# Docker
./review-docker.sh <pr-url> [options]
./review-docker.sh setup
./review-docker.sh build
```

### CLI Reference

#### `review-tool review <pr-url>`

Review a GitHub pull request.

```
Options:
  -c, --config PATH        Config file (default: review_tool.json)
  -s, --skills TEXT        Comma-separated skills to run
  -v, --verbosity INT      0=summary, 1=normal, 2=detailed, 3=debug
  --dry-run                Print review to stdout, don't post
  -m, --model TEXT         Override Claude model
  --max-budget FLOAT       Override max USD per skill
  --no-graph               Skip code_graph_search
  -g, --guidance PATH      Extra guidance markdown file
  -o, --output PATH        Write review to file
```

**Examples:**

```bash
# Standard review
review-tool review https://github.com/owner/repo/pull/42

# Dry-run with detailed verbosity
review-tool review https://github.com/owner/repo/pull/42 --dry-run -v 2

# Security-only review with custom guidance
review-tool review https://github.com/owner/repo/pull/42 -s security -g ./security-rules.md

# Quick summary review with low budget
review-tool review https://github.com/owner/repo/pull/42 -v 0 --max-budget 0.25

# Write to file instead of posting
review-tool review https://github.com/owner/repo/pull/42 -o review-output.md
```

#### `review-tool skills list`

Show all available skills and whether they're enabled.

```bash
$ review-tool skills list
              Available Skills
┏━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name        ┃ Enabled ┃ Description                            ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ defects     │ yes     │ Detect bugs, logic errors, race...     │
│ go          │ yes     │ Go-specific: error handling, gorout... │
│ java        │ yes     │ Java-specific: null safety, concur... │
│ performance │ no      │ Find performance issues: N+1 quer...  │
│ quality     │ yes     │ Review code quality, maintainabil...  │
│ rust        │ yes     │ Rust-specific: unsafe, ownership,...  │
│ security    │ yes     │ Find security vulnerabilities, in...  │
│ typescript  │ yes     │ TypeScript-specific: type safety,...  │
└─────────────┴─────────┴────────────────────────────────────────┘
```

#### `review-tool skills show <name>`

Show a skill's description, system prompt, and allowed tools.

#### `review-tool config init [--path PATH]`

Generate a default configuration file.

#### `review-tool config check [--config PATH]`

Validate a configuration file and display a summary.

### Verbosity Levels

| Level | Flag | Behavior |
|-------|------|----------|
| 0 | `-v 0` | Summary only. Reports only critical and high severity findings. Minimal descriptions. |
| 1 | `-v 1` | Normal (default). Full findings with descriptions and suggestions. |
| 2 | `-v 2` | Detailed. Claude reads full files and surrounding code. Traces data flows and call chains. Includes per-skill summaries in the review. |
| 3 | `-v 3` | Debug. Same analysis as level 2 with debug-level logging to stderr. |

Higher verbosity uses more tokens and takes longer, but catches more subtle issues.

### Custom Guidance

Provide a markdown file with additional review instructions that apply to all skills:

```bash
review-tool review <pr-url> -g ./my-guidance.md
```

Example guidance file:

```markdown
## Project-Specific Rules

- All database queries must use parameterized statements
- API endpoints must check the `X-Request-ID` header
- Do not flag TODOs as issues — they are tracked in Linear
- Authentication middleware is in `src/middleware/auth.ts`
```

You can also set this permanently in the config:

```json
{
  "guidance_file": "/path/to/team-review-rules.md"
}
```

The guidance text is appended to every skill's system prompt.

---

## Architecture

### Review Pipeline

The pipeline (`pipeline.py`) executes these stages sequentially:

```
PR URL
  │
  ├─ 1. Fetch PR ─────── gh pr view + gh pr diff
  │
  ├─ 2. Checkout ─────── gh pr checkout into temp dir
  │
  ├─ 3. Index ────────── Start code_graph_search, index repo
  │
  ├─ 4. Run Skills ───── For each enabled skill:
  │     │                  a. pre_analyze() — graph queries
  │     │                  b. build prompts — system + review
  │     │                  c. claude -p    — headless invocation
  │     │                  d. parse output — extract findings
  │     └─────────────── Repeat for next skill
  │
  ├─ 5. Aggregate ────── Deduplicate, sort by severity
  │
  ├─ 6. Format ───────── Markdown body + inline comments
  │
  └─ 7. Post ─────────── gh pr review + gh api (inline comments)
```

Each skill runs as a **separate Claude Code invocation** with its own focused system prompt. This produces better results than a single overloaded prompt, allows per-skill budget control, and makes skills independently testable.

### Module Overview

```
src/review_tool/
├── __main__.py          Typer CLI entry point
├── pipeline.py          Orchestrator — ties all stages together
├── claude.py            Wrapper around `claude -p` (headless mode)
├── github.py            Wrapper around `gh` CLI
├── graph_client.py      httpx client for code_graph_search REST API
├── graph_lifecycle.py   Manages code_graph_search Java subprocess
├── config.py            Pydantic models + JSON loader with env interpolation
├── models.py            Data models: Finding, PRData, ReviewContext, etc.
├── prompt_builder.py    Assembles system + review prompts per skill
├── formatter.py         Findings → GitHub markdown + inline comments
└── skills/
    ├── __init__.py      BaseSkill ABC + SkillRegistry
    ├── defects.py       Bug/logic error detection
    ├── security.py      OWASP/CWE vulnerability review
    ├── quality.py       Code quality & maintainability
    └── performance.py   Performance issue detection
```

### Skills System

The skills system is the primary extension point. Each skill:

1. Provides a **system prompt** that focuses Claude on a specific concern (verbosity-dependent)
2. Builds a **review prompt** containing the PR diff, file list, and optional graph analysis
3. Optionally runs **pre-analysis** via code_graph_search (e.g., find callers of changed functions)
4. **Parses Claude's output** into structured `Finding` objects

Built-in skills:

**General skills** (language-agnostic, run on every PR):

| Skill | Focus | Pre-analysis |
|-------|-------|-------------|
| `defects` | Bugs, null access, race conditions, resource leaks, logic errors | Finds callers of changed methods |
| `security` | OWASP Top 10, CWE, injection, auth, data exposure, crypto | Finds input handler functions |
| `quality` | Naming, complexity, duplication, design patterns, dead code | — |
| `performance` | N+1 queries, memory leaks, algorithm complexity, blocking I/O | Finds call chains through changed code |

**Language-specific skills** (auto-skip when no matching files in PR):

| Skill | Focus | Pre-analysis |
|-------|-------|-------------|
| `java` | NullPointerException, concurrency (synchronized, volatile), resource leaks (try-with-resources), Spring/JPA patterns, Stream API, serialization | Class hierarchies via graph |
| `rust` | Unsafe soundness, ownership/borrowing, lifetime design, async pitfalls (MutexGuard across .await), error handling (unwrap in lib code), FFI safety | Trait/struct/enum members via graph |
| `go` | Error handling (unchecked err), goroutine leaks, channel deadlocks, nil map/interface, context propagation, sync.Mutex patterns | Interface/struct definitions via graph |
| `typescript` | Type safety (any, as, !), async/await pitfalls, React hooks (stale closures, missing deps), null handling, runtime/type mismatches, enum pitfalls | Type graph (callers/callees) via graph |

### code_graph_search Integration

[code_graph_search](https://github.com/geekychris/code_graph_search) is a Java application that builds a searchable graph of code structure: functions, classes, call relationships, type hierarchies, and imports across multiple languages.

**Two integration modes:**

| Mode | How it Works | When to Use |
|------|-------------|-------------|
| **REST** (default) | review-tool calls the REST API in `pre_analyze()`, injects results into the Claude prompt as JSON context | Cheaper, more predictable. Good for most reviews. |
| **MCP** | code_graph_search runs as an MCP server that Claude can call directly with 50+ tools | More powerful — Claude can autonomously explore call chains. Uses more tokens. Enable via `graph.mcp_mode: true`. |

**Lifecycle management:** If `graph.jar_path` is set in config, review-tool starts code_graph_search as a subprocess, generates a config YAML for the target repo, waits for it to be ready, and shuts it down after the review. If not set, it assumes code_graph_search is running externally at `graph.host`.

### Claude Code Integration

Reviews are performed by invoking `claude -p` (print/headless mode) as a subprocess:

```
claude -p "<review prompt>"
  --output-format json
  --model <model>
  --max-turns <N>
  --max-budget-usd <budget>
  --permission-mode bypassPermissions
  --append-system-prompt "<skill system prompt + guidance>"
  --allowedTools "Bash,Read,Grep,Glob"
  --add-dir <repo-checkout-dir>
  [--mcp-config <path>]        # if graph MCP mode enabled
```

Claude has full filesystem access to the checked-out repository, so it can read complete files, grep for patterns, and explore beyond the diff.

### Output Formatting

Findings are formatted as a GitHub review:

**Review body** (posted via `gh pr review --body`):
- Header with skill list and finding counts
- Findings grouped by severity with emoji indicators (🔴🟠🟡🔵ℹ️)
- Each finding shows: title, file:line, category, description, suggestion
- Collapsed per-skill summaries at verbosity >= 2

**Inline comments** (posted via `gh api`):
- Each finding with a specific file + line becomes an inline comment on the PR diff
- Comment body includes severity, title, description, and suggestion

**Review event:**
- `REQUEST_CHANGES` if any critical or high findings
- `COMMENT` if only medium/low/info findings
- `APPROVE` if no findings

---

## Extending review-tool

### Writing a Custom Skill

1. Create a Python file in your custom skills directory:

```python
# my_skills/accessibility.py

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry


class AccessibilitySkill(BaseSkill):

    @property
    def name(self) -> str:
        return "accessibility"

    @property
    def description(self) -> str:
        return "Check for accessibility (a11y) patterns in frontend code"

    def system_prompt(self, verbosity: int = 1) -> str:
        return """\
You are an accessibility expert reviewing frontend code.

Look for:
- Missing alt text on images
- Missing ARIA labels and roles
- Keyboard navigation issues (missing tabIndex, focus traps)
- Color contrast violations
- Non-semantic HTML (div soup instead of proper elements)
- Missing form labels and fieldset/legend
- Dynamic content not announced to screen readers

Report format:
**[SEVERITY]** `file:line` - Title
Description of the accessibility issue.
> Suggestion: How to fix it."""

    def build_review_prompt(self, context: ReviewContext) -> str:
        # Only review frontend files
        frontend_files = [
            f for f in context.pr.files
            if f.path.endswith(('.tsx', '.jsx', '.html', '.vue', '.svelte', '.css'))
        ]
        if not frontend_files:
            return ""  # Skip — no frontend files in this PR

        pr = context.pr
        prompt = "Review this PR for accessibility issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += "\n## Changed Frontend Files\n"
        for f in frontend_files:
            prompt += f"- `{f.path}` ({f.status})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += "\nRead the full files. Report findings in the specified format."
        return prompt


# Register the skill
SkillRegistry.register(AccessibilitySkill())
```

2. Point your config at the directory:

```json
{
  "skills": {
    "enabled": ["defects", "security", "quality", "accessibility"],
    "custom_skills_dir": "/path/to/my_skills"
  }
}
```

3. The skill is automatically discovered and available:

```bash
review-tool skills list     # Shows "accessibility" in the table
review-tool review <url> -s accessibility
```

### Skill API Reference

Subclass `BaseSkill` and implement these:

| Method | Required | Returns | Purpose |
|--------|----------|---------|---------|
| `name` | Yes (property) | `str` | Unique identifier (used in CLI, config, finding category) |
| `description` | Yes (property) | `str` | Human-readable one-liner |
| `system_prompt(verbosity)` | Yes | `str` | Claude's system prompt. Use verbosity to control depth (0=concise, 2=exhaustive). |
| `build_review_prompt(context)` | Yes | `str` | The review prompt with PR data. Return `""` to skip this PR. |
| `allowed_tools()` | No | `list[str]` | Default: `["Bash", "Read", "Grep", "Glob"]`. Add more if your skill needs them. |
| `pre_analyze(context)` | No | `dict` | Run code_graph_search queries. Returned dict is injected as JSON into the Claude prompt. |
| `parse_findings(raw_text)` | No | `list[Finding]` | Parse Claude's output. Default parser expects `**[SEVERITY]** \`file:line\` - Title` format. |
| `max_budget_usd()` | No | `float \| None` | Per-skill budget override. `None` uses global config value. |

The `ReviewContext` object passed to skills contains:

```python
context.pr              # PRData: url, title, body, author, files, diff_text, labels
context.repo_dir        # str: path to checked-out repo
context.graph_client    # GraphClient | None: code_graph_search client
context.config          # AppConfig: full configuration
context.verbosity       # int: 0-3
context.guidance        # str: custom guidance text
```

### Custom Guidance Files

For review rules that apply across all skills (not specific enough to warrant a custom skill), use guidance files:

```bash
review-tool review <url> -g ./team-rules.md
```

Or set permanently:

```json
{ "guidance_file": "./team-rules.md" }
```

The guidance markdown is appended to every skill's system prompt. Use it for:
- Project-specific coding standards
- Known false-positive suppressions
- Focus areas ("pay extra attention to the payment module")
- Context ("we're migrating from REST to GraphQL, flag any new REST endpoints")

For something more structural (custom system prompt, file filtering, pre-analysis), write a [custom skill](#writing-a-custom-skill) instead.

---

## Docker Details

### What's in the Image

The Docker image is a multi-stage build:

1. **Build stage** — clones and builds code_graph_search JAR from source using Java 21 JDK + Maven
2. **Runtime stage** — eclipse-temurin:21-jre with:
   - Python 3 + review-tool installed
   - Node.js 22 + Claude Code CLI (`@anthropic-ai/claude-code`)
   - gh CLI
   - code_graph_search JAR at `/opt/code-graph-search/code-graph-search.jar`

### Volume Mounts

| Mount | Purpose |
|-------|---------|
| `~/.claude → /root/.claude` | Claude Code OAuth credentials (Max Pro). Mounted read-only. |
| `review_tool.json → /config/review_tool.json` | Configuration file. |
| `review_repos → /repos` | Named volume for cached repo checkouts between runs. |

### Running via Docker

```bash
# Using the wrapper script
./review-docker.sh https://github.com/owner/repo/pull/123

# Using docker compose directly
docker compose run review-tool \
  review-tool review https://github.com/owner/repo/pull/123 \
  -c /config/review_tool.json

# Using docker run directly
docker run --rm -it \
  -e GH_TOKEN=$GH_TOKEN \
  -v ~/.claude:/root/.claude:ro \
  -v $(pwd)/review_tool.json:/config/review_tool.json:ro \
  review-tool \
  review-tool review https://github.com/owner/repo/pull/123 -c /config/review_tool.json
```

### Rebuilding

```bash
./review-docker.sh build
# or
docker compose build
```

---

## Development

```bash
# Setup dev environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Run tests
pip install pytest
pytest

# Verify all imports
python3 -c "from review_tool.pipeline import run_review; print('OK')"

# Test CLI
review-tool --help
review-tool skills list
review-tool config init --path /tmp/test.json
review-tool config check --config /tmp/test.json

# Dry-run a review
review-tool review https://github.com/owner/repo/pull/1 --dry-run
```

### Project Structure

```
review_tool/
├── pyproject.toml           Package metadata and dependencies
├── config.example.json      Template configuration
├── review_tool.json         Your local config (gitignored)
├── .env                     Docker env vars (gitignored)
├── review.sh                Native macOS/Linux runner
├── review-docker.sh         Docker runner
├── Dockerfile               Multi-stage build
├── docker-compose.yml       Docker Compose config
├── CLAUDE.md                Claude Code project guidance
├── src/review_tool/         Python package
│   ├── __main__.py          CLI entry point
│   ├── pipeline.py          Review orchestrator
│   ├── claude.py            Claude Code headless wrapper
│   ├── github.py            GitHub gh CLI wrapper
│   ├── graph_client.py      code_graph_search REST client
│   ├── graph_lifecycle.py   code_graph_search process manager
│   ├── config.py            Configuration models
│   ├── models.py            Data models
│   ├── prompt_builder.py    Prompt assembly
│   ├── formatter.py         Output formatting
│   └── skills/              Review skills
│       ├── __init__.py      BaseSkill ABC + SkillRegistry
│       ├── defects.py       Bug detection
│       ├── security.py      Security review
│       ├── quality.py       Code quality
│       ├── performance.py   Performance analysis
│       ├── lang_java.py     Java-specific review
│       ├── lang_rust.py     Rust-specific review
│       ├── lang_go.py       Go-specific review
│       └── lang_typescript.py TypeScript-specific review
└── tests/                   Test suite
```
