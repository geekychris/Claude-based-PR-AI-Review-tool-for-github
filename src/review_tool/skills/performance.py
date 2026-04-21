"""Performance review skill — finds performance issues and inefficiencies."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    0: """\
You are a code reviewer focused on performance. Report only critical performance issues. Be extremely concise.""",
    1: """\
You are a performance-focused code reviewer.

Look for:
- N+1 queries and unnecessary database round-trips
- Missing or incorrect caching
- Unbounded data fetching (no pagination, no limits)
- Expensive operations in hot paths (loops, request handlers)
- Memory leaks and unnecessary allocations
- Blocking I/O in async contexts
- Inefficient algorithms (O(n²) when O(n) is possible)
- Missing indexes for query patterns
- Unnecessary serialization/deserialization

Report format:
**[SEVERITY]** `file:line` - Title
Why this is a performance concern and estimated impact.
> Suggestion: How to optimize.""",
    2: """\
You are a performance engineer reviewing a pull request for efficiency issues.

Read full files and trace execution paths to understand the performance characteristics. Use code graph tools to find hot paths and call chains.

Analyze:
- Database query patterns (N+1, missing indexes, full table scans)
- Caching strategy (missing cache, cache invalidation, TTL issues)
- Data volume handling (unbounded fetches, missing pagination)
- Algorithm complexity in context of expected data sizes
- Memory allocation patterns (unnecessary copies, retained references)
- Concurrency efficiency (lock contention, unnecessary synchronization)
- I/O patterns (blocking in async, sequential when parallel is possible)
- Network efficiency (chatty APIs, missing batching)
- Resource pool management (connection pools, thread pools)

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed performance analysis including expected impact at scale.
> Suggestion: Specific optimization with complexity analysis.""",
}


class PerformanceSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "performance"

    @property
    def description(self) -> str:
        return "Find performance issues: N+1 queries, memory leaks, algorithmic inefficiency"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        pr = context.pr
        prompt = "Review this PR for performance issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Files\n"
        for f in pr.files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full files to understand execution context. "
            "Trace hot paths through the changed code. "
            "Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        """Use graph search to find call chains through changed code."""
        if not context.graph_client:
            return {}

        extra: dict[str, Any] = {}
        call_chains = []

        for fc in context.pr.files:
            if fc.status == "removed":
                continue
            try:
                results = context.graph_client.search(
                    fc.path, element_type="METHOD", limit=10
                )
                for elem in results[:3]:
                    eid = elem.get("id", "")
                    if not eid:
                        continue
                    callees = context.graph_client.get_callees(eid)
                    if callees:
                        call_chains.append(
                            {
                                "method": elem.get("qualifiedName", elem.get("name", "")),
                                "calls": [
                                    c.get("qualifiedName", c.get("name", ""))
                                    for c in callees[:10]
                                ],
                            }
                        )
            except Exception:
                log.debug("Graph query failed for %s", fc.path, exc_info=True)

        if call_chains:
            extra["call_chains"] = call_chains
        return extra


SkillRegistry.register(PerformanceSkill())
