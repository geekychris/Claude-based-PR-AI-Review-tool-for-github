"""TypeScript-specific review skill."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")

SYSTEM_PROMPTS = {
    0: """\
You are a TypeScript expert reviewer. Report only critical and high severity TypeScript-specific issues. Be extremely concise.""",
    1: """\
You are a senior TypeScript engineer performing a language-specific code review.

Look for TypeScript-specific issues:
- Type safety: `any` usage, type assertions (as) bypassing checks, non-null assertions (!), missing discriminated unions
- Null/undefined: missing optional chaining, incorrect nullish coalescing, strict null check gaps
- Async: unhandled promise rejections, missing await, floating promises, async void functions, race conditions in state updates
- Type design: incorrect generics, missing readonly, overly broad types, missing branded types for IDs
- Runtime mismatches: TypeScript types that don't match runtime behavior (JSON parsing, API responses, env vars)
- React (if .tsx): missing keys, stale closure in useEffect, missing dependency arrays, incorrect memo/useMemo/useCallback usage
- Import/export: circular dependencies, barrel file re-export issues, missing type-only imports
- Enums: numeric enum pitfalls (reverse mapping), preferring const enum or union types
- Error handling: untyped catch blocks, missing error narrowing, throwing non-Error objects
- Module patterns: CommonJS/ESM interop issues, incorrect default export usage
- Zod/io-ts/valibot: schema diverging from TypeScript type, missing runtime validation at boundaries

Report format:
**[SEVERITY]** `file:line` - Title
Description of the TypeScript-specific issue.
> Suggestion: Type-safe fix with proper TypeScript idioms.""",
    2: """\
You are a TypeScript architect performing a thorough language-specific review.

Read full files and trace type flow through the codebase. Examine tsconfig.json, package.json, and related config for strictness settings and dependency concerns.

Analyze all items from the standard review plus:
- Type narrowing: missed discriminated unions, incorrect type guards, assertion function correctness
- Generic design: unnecessary generics, missing constraints, conditional type pitfalls, infer usage
- Declaration merging: interface augmentation issues, module augmentation correctness
- Mapped/conditional types: correctness of complex type transformations, missing distributive handling
- Covariance/contravariance: incorrect function type assignments, mutable array covariance
- React patterns (tsx): context design, render prop types, forward ref typing, event handler types, server component boundaries
- Next.js/Remix: server/client boundary type safety, loader type propagation, serialization boundaries
- Build: tree-shaking barriers (side effects), bundle size from type-only imports, declaration file issues
- Monorepo: workspace type resolution, path mapping correctness, composite project references
- Test types: incorrect mock types, missing type assertions in tests, test utility type safety
- API contract: request/response type accuracy, OpenAPI/GraphQL codegen alignment

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed analysis with type-level implications and framework context.
> Suggestion: Type-safe fix with code example.""",
}


class TypeScriptSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "typescript"

    @property
    def description(self) -> str:
        return "TypeScript-specific review: type safety, async patterns, React/framework issues"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        ts_files = [
            f for f in context.pr.files
            if f.path.endswith(TS_EXTENSIONS)
            or f.path in ("tsconfig.json", "package.json")
        ]
        if not ts_files:
            return ""

        pr = context.pr
        prompt = "Review this PR for TypeScript-specific issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed TypeScript Files\n"
        for f in ts_files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full TypeScript files for context. Check type safety, "
            "async patterns, null handling, React patterns (if .tsx), and "
            "runtime/type mismatches. Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        if not context.graph_client:
            return {}

        extra: dict[str, Any] = {}
        type_info = []

        for fc in context.pr.files:
            if not fc.path.endswith(TS_EXTENSIONS) or fc.status == "removed":
                continue
            try:
                results = context.graph_client.search(fc.path, limit=10)
                for elem in results[:5]:
                    eid = elem.get("id", "")
                    etype = elem.get("elementType", "")
                    if not eid:
                        continue
                    if etype in ("CLASS", "INTERFACE", "FUNCTION"):
                        callers = context.graph_client.get_callers(eid)
                        callees = context.graph_client.get_callees(eid)
                        info: dict[str, Any] = {
                            "type": etype,
                            "name": elem.get("qualifiedName", elem.get("name", "")),
                        }
                        if callers:
                            info["callers"] = [
                                c.get("qualifiedName", c.get("name", ""))
                                for c in callers[:10]
                            ]
                        if callees:
                            info["callees"] = [
                                c.get("qualifiedName", c.get("name", ""))
                                for c in callees[:10]
                            ]
                        type_info.append(info)
            except Exception:
                log.debug("Graph query failed for %s", fc.path, exc_info=True)

        if type_info:
            extra["type_graph"] = type_info
        return extra


SkillRegistry.register(TypeScriptSkill())
