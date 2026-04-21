"""Defect detection skill — finds bugs, logic errors, and correctness issues."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    0: """\
You are a code reviewer focused on finding defects. Report only critical and high severity bugs. Be extremely concise.""",
    1: """\
You are a senior code reviewer focused on finding defects and correctness issues.

Look for:
- Null/undefined access, uninitialized variables
- Off-by-one errors, boundary conditions
- Resource leaks (unclosed files, connections, streams)
- Race conditions and concurrency bugs
- Incorrect error handling (swallowed exceptions, wrong error types)
- Logic errors (wrong operators, inverted conditions, missing cases)
- Type mismatches and unsafe casts
- API contract violations

For each finding, report:
**[SEVERITY]** `file:line` - Title
Description of the issue and why it's a problem.
> Suggestion: How to fix it.""",
    2: """\
You are an expert code reviewer performing a thorough defect analysis.

Examine every changed file AND the surrounding code for full context. Use the codebase tools to read related files, check callers, and understand data flow.

Look for:
- Null/undefined access, uninitialized variables
- Off-by-one errors, boundary conditions, integer overflow
- Resource leaks (unclosed files, connections, streams, locks)
- Race conditions, deadlocks, and concurrency bugs
- Incorrect error handling (swallowed exceptions, wrong error types, missing cleanup)
- Logic errors (wrong operators, inverted conditions, missing cases, unreachable code)
- Type mismatches and unsafe casts
- API contract violations (wrong argument order, missing required fields)
- State management bugs (stale state, missing updates)
- Edge cases in input handling

For each finding, provide detailed analysis including the execution path that leads to the bug.

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed description including the path to trigger the bug.
> Suggestion: Specific code fix.""",
}


class DefectsSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "defects"

    @property
    def description(self) -> str:
        return "Detect bugs, logic errors, null pointer issues, race conditions, and resource leaks"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        pr = context.pr
        prompt = f"Review this PR for defects and correctness issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += f"\n## Changed Files\n"
        for f in pr.files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nExamine the full files for context around the changes. "
            "Read related files if needed to understand the impact of changes. "
            "Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        """Use code_graph_search to find callers of changed functions."""
        if not context.graph_client:
            return {}

        extra: dict[str, Any] = {}
        callers_info = []

        for fc in context.pr.files:
            if fc.status == "removed":
                continue
            try:
                results = context.graph_client.search(
                    fc.path, element_type="METHOD", limit=10
                )
                for elem in results[:5]:
                    eid = elem.get("id", "")
                    if not eid:
                        continue
                    callers = context.graph_client.get_callers(eid)
                    if callers:
                        callers_info.append(
                            {
                                "method": elem.get("qualifiedName", elem.get("name", "")),
                                "file": fc.path,
                                "callers": [
                                    c.get("qualifiedName", c.get("name", ""))
                                    for c in callers[:10]
                                ],
                            }
                        )
            except Exception:
                log.debug("Graph query failed for %s", fc.path, exc_info=True)

        if callers_info:
            extra["callers"] = callers_info
        return extra


SkillRegistry.register(DefectsSkill())
