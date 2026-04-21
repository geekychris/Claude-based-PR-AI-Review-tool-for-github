"""Configuration models and loader."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import BaseModel


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} patterns with environment variable values."""

    def _replace(match: re.Match) -> str:
        var = match.group(1)
        result = os.environ.get(var, "")
        if not result:
            # Support ${VAR:-default} syntax
            if ":-" in var:
                var_name, default = var.split(":-", 1)
                return os.environ.get(var_name, default)
        return result

    return re.sub(r"\$\{([^}]+)}", _replace, value)


def _interpolate_recursive(obj: object) -> object:
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(v) for v in obj]
    return obj


class GithubConfig(BaseModel):
    token: str = "${GH_TOKEN}"

    def resolved_token(self) -> str:
        return _interpolate_env(self.token)


class ClaudeConfig(BaseModel):
    model: str = "sonnet"
    max_turns: int = 30
    max_budget_usd: float = 1.0
    allowed_tools: list[str] = ["Bash", "Read", "Grep", "Glob"]
    permission_mode: str = "bypassPermissions"
    extra_system_prompt: str = ""


class GraphConfig(BaseModel):
    enabled: bool = True
    host: str = "http://localhost:8080"
    startup_timeout_seconds: int = 120
    jar_path: str | None = None
    config_path: str | None = None
    mcp_mode: bool = False


class SkillsConfig(BaseModel):
    enabled: list[str] = ["defects", "security", "quality", "java", "rust", "go", "typescript"]
    custom_skills_dir: str | None = None


class AppConfig(BaseModel):
    github: GithubConfig = GithubConfig()
    claude: ClaudeConfig = ClaudeConfig()
    graph: GraphConfig = GraphConfig()
    skills: SkillsConfig = SkillsConfig()
    verbosity: int = 1
    repo_checkout_dir: str = "/tmp/review_tool_repos"
    guidance_file: str | None = None


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load config from JSON file, applying env var interpolation."""
    if path is None:
        path = Path("review_tool.json")
    else:
        path = Path(path)

    if not path.exists():
        return AppConfig()

    raw = json.loads(path.read_text())
    interpolated = _interpolate_recursive(raw)
    return AppConfig.model_validate(interpolated)


def generate_default_config(path: Path | str) -> Path:
    """Write a default config file."""
    path = Path(path)
    config = AppConfig()
    data = config.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path
