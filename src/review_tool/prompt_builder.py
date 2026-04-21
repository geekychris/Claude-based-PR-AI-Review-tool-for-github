"""Assembles prompts from skill definitions, PR context, and custom guidance."""

from __future__ import annotations

import json
import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill

log = logging.getLogger(__name__)

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": ["integer", "null"]},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "suggestion": {"type": ["string", "null"]},
                },
                "required": ["file", "line_start", "severity", "title", "description"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["findings", "summary"],
}


def build_system_prompt(
    skill: BaseSkill,
    context: ReviewContext,
) -> str:
    """Build the complete system prompt for a skill invocation."""
    parts = [skill.system_prompt(context.verbosity)]

    # Add output format instructions
    parts.append(
        "\n\nIMPORTANT: Structure your response with findings in this format:\n"
        "**[SEVERITY]** `file:line` - Title\n"
        "Description\n"
        "> Suggestion: fix\n\n"
        "End with a ## Summary section.\n"
        "Severity levels: critical, high, medium, low, info."
    )

    # Add custom guidance
    if context.guidance:
        parts.append(f"\n\n## Additional Review Guidance\n{context.guidance}")

    # Add extra system prompt from config
    if context.config.claude.extra_system_prompt:
        parts.append(f"\n\n{context.config.claude.extra_system_prompt}")

    return "\n".join(parts)


def build_review_prompt(
    skill: BaseSkill,
    context: ReviewContext,
    extra_context: dict[str, Any] | None = None,
) -> str:
    """Build the complete review prompt including PR data and graph analysis."""
    prompt = skill.build_review_prompt(context)
    if not prompt:
        return ""

    # Inject graph pre-analysis results
    if extra_context:
        prompt += "\n\n## Code Graph Analysis\n"
        prompt += "The following relationships were found via static analysis:\n\n"
        prompt += "```json\n"
        prompt += json.dumps(extra_context, indent=2)
        prompt += "\n```\n"

    return prompt
