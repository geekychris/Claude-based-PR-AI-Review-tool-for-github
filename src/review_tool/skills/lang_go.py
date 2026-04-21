"""Go-specific review skill."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

GO_EXTENSIONS = (".go",)

SYSTEM_PROMPTS = {
    0: """\
You are a Go expert reviewer. Report only critical and high severity Go-specific issues. Be extremely concise.""",
    1: """\
You are a senior Go engineer performing a language-specific code review.

Look for Go-specific issues:
- Error handling: ignored errors (unchecked err return), errors.Is/As misuse, wrapping without context, sentinel vs custom errors
- Goroutine leaks: goroutines that never terminate, missing context cancellation, unbounded goroutine spawning
- Concurrency: data races on shared state, incorrect sync.Mutex usage, channel misuse (deadlocks, premature close, send on closed)
- Resource leaks: unclosed io.Closer (files, HTTP bodies, DB rows), defer in loops, missing resp.Body.Close()
- nil pitfalls: nil interface vs nil pointer, nil map writes, nil slice append (ok) vs nil map access (panic)
- Context: not propagating context, using context.Background() when parent context is available, storing context in structs
- Slices: unexpected aliasing via append, large backing array retention, missing copy for safety
- Interface design: too-large interfaces, accepting interfaces returning structs, empty interface{}/any overuse
- Package design: circular imports, internal package misuse, exported API surface too broad
- Testing: table-driven test anti-patterns, missing t.Helper(), test pollution via shared state
- Go modules: replace directives left in, indirect dependency issues, missing go.sum updates

Report format:
**[SEVERITY]** `file:line` - Title
Description of the Go-specific issue.
> Suggestion: Idiomatic Go fix.""",
    2: """\
You are a Go expert performing a thorough language-specific review.

Read full files and trace goroutine lifecycles, error propagation chains, and interface satisfaction. Check go.mod for dependency concerns.

Analyze all items from the standard review plus:
- Race conditions: analyze with `go vet -race` mindset, check all shared mutable state
- Memory model: happens-before relationships, atomic operations correctness
- Generics (Go 1.18+): type constraint design, unnecessary generic code, missing constraints
- CGo: memory management across boundary, pointer passing rules, callback safety
- Reflection: type assertion chains, missing type switches, runtime panics from reflect
- init() functions: ordering dependencies, hidden side effects, test isolation issues
- Build tags and conditional compilation correctness
- Allocation patterns: escape analysis awareness, sync.Pool usage, string/byte conversions
- HTTP: handler goroutine lifecycle, middleware ordering, timeout propagation
- Database: sql.Rows not closed, transaction rollback in defer, connection pool exhaustion

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed analysis with goroutine lifecycle and error propagation context.
> Suggestion: Idiomatic fix with code example.""",
}


class GoSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "go"

    @property
    def description(self) -> str:
        return "Go-specific review: error handling, goroutine leaks, concurrency, nil safety"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        go_files = [
            f for f in context.pr.files
            if f.path.endswith(GO_EXTENSIONS) or f.path in ("go.mod", "go.sum")
        ]
        if not go_files:
            return ""

        pr = context.pr
        prompt = "Review this PR for Go-specific issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Go Files\n"
        for f in go_files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full Go source files. Check error handling chains, "
            "goroutine lifecycles, channel usage, context propagation, and nil safety. "
            "Also check go.mod if changed. Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        if not context.graph_client:
            return {}

        extra: dict[str, Any] = {}
        interface_info = []

        for fc in context.pr.files:
            if not fc.path.endswith(GO_EXTENSIONS) or fc.status == "removed":
                continue
            try:
                results = context.graph_client.search(fc.path, limit=10)
                for elem in results[:5]:
                    eid = elem.get("id", "")
                    etype = elem.get("elementType", "")
                    if not eid:
                        continue
                    if etype in ("INTERFACE", "STRUCT"):
                        children = context.graph_client.get_children(eid)
                        callers = context.graph_client.get_callers(eid) if etype == "FUNCTION" else []
                        info: dict[str, Any] = {
                            "type": etype,
                            "name": elem.get("qualifiedName", elem.get("name", "")),
                            "members": [c.get("name", "") for c in children[:15]],
                        }
                        if callers:
                            info["callers"] = [
                                c.get("qualifiedName", c.get("name", ""))
                                for c in callers[:10]
                            ]
                        interface_info.append(info)
            except Exception:
                log.debug("Graph query failed for %s", fc.path, exc_info=True)

        if interface_info:
            extra["type_definitions"] = interface_info
        return extra


SkillRegistry.register(GoSkill())
