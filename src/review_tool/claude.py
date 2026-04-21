"""Claude Code CLI headless mode wrapper."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


class ClaudeError(Exception):
    """Raised when a Claude Code CLI invocation fails."""


@dataclass
class ClaudeResult:
    """Parsed result from a Claude Code headless invocation."""

    result: str
    session_id: str = ""
    cost_usd: float = 0.0
    raw: dict | None = None


def invoke(
    prompt: str,
    *,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    add_dirs: list[str] | None = None,
    mcp_config: str | None = None,
    model: str = "sonnet",
    max_turns: int = 30,
    max_budget_usd: float = 1.0,
    permission_mode: str = "bypassPermissions",
    json_schema: dict | None = None,
    timeout: int = 600,
) -> ClaudeResult:
    """Run claude -p and return parsed output.

    Args:
        prompt: The review prompt to send.
        system_prompt: Appended to Claude's system prompt.
        allowed_tools: Which tools Claude can use.
        add_dirs: Additional directories for Claude to access.
        mcp_config: Path to MCP config JSON for code_graph_search.
        model: Claude model to use.
        max_turns: Max agentic turns.
        max_budget_usd: Max spend per invocation.
        permission_mode: Permission mode for tool access.
        json_schema: If set, request structured JSON output matching this schema.
        timeout: Subprocess timeout in seconds.

    Returns:
        ClaudeResult with the response text and metadata.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
        "--permission-mode",
        permission_mode,
    ]

    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if add_dirs:
        for d in add_dirs:
            cmd += ["--add-dir", d]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema)]

    log.debug("Running: %s", " ".join(cmd[:10]) + "...")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        log.error("Claude stderr: %s", result.stderr[:500])
        raise ClaudeError(
            f"Claude exited with code {result.returncode}: {result.stderr[:200]}"
        )

    # Parse JSON output
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        # If JSON parsing fails, treat stdout as plain text
        return ClaudeResult(result=result.stdout.strip())

    # Claude --output-format json returns different shapes:
    # - With --verbose: a JSON array of streaming events
    # - Without --verbose: a single object with "result"
    if isinstance(data, list):
        # Extract text from the array of message events
        text_parts = []
        session_id = ""
        cost = 0.0
        for event in data:
            if isinstance(event, dict):
                # Top-level result message
                if "result" in event:
                    text_parts.append(str(event["result"]))
                    session_id = event.get("session_id", session_id)
                    cost = event.get("cost_usd", cost)
                # Streaming assistant message content
                elif event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block["text"])
                elif event.get("type") == "result":
                    text_parts.append(str(event.get("result", "")))
                    session_id = event.get("session_id", session_id)
                    cost = event.get("cost_usd", cost)
        result_text = "\n".join(text_parts) if text_parts else result.stdout
        return ClaudeResult(
            result=result_text,
            session_id=session_id,
            cost_usd=cost,
            raw=data,
        )

    return ClaudeResult(
        result=data.get("result", result.stdout),
        session_id=data.get("session_id", ""),
        cost_usd=data.get("cost_usd", 0.0),
        raw=data,
    )


def invoke_with_files(
    prompt: str,
    file_contents: dict[str, str],
    **kwargs: object,
) -> ClaudeResult:
    """Invoke Claude with file contents embedded in the prompt.

    Useful when you want Claude to review specific files without
    needing filesystem access.
    """
    file_section = "\n\n".join(
        f"### File: `{path}`\n```\n{content}\n```" for path, content in file_contents.items()
    )
    full_prompt = f"{prompt}\n\n## Files for Review\n\n{file_section}"
    return invoke(full_prompt, **kwargs)
