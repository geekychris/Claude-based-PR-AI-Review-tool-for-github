"""Java-specific review skill."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

JAVA_EXTENSIONS = (".java",)

SYSTEM_PROMPTS = {
    0: """\
You are a Java expert reviewer. Report only critical and high severity Java-specific issues. Be extremely concise.""",
    1: """\
You are a senior Java engineer performing a language-specific code review.

Look for Java-specific issues:
- NullPointerException risks: missing null checks, Optional misuse, nullable annotations ignored
- Resource leaks: unclosed streams, connections, or locks not in try-with-resources
- Concurrency bugs: unsynchronized shared state, race conditions, improper use of volatile/atomic, deadlock patterns
- Exception anti-patterns: catching Exception/Throwable, empty catch blocks, swallowing InterruptedException
- Collections misuse: ConcurrentModificationException risks, wrong Map/List choice, missing generics
- Memory issues: large object retention, String concatenation in loops (use StringBuilder), autoboxing in hot paths
- Serialization vulnerabilities: missing serialVersionUID, deserializing untrusted data
- API misuse: equals/hashCode contract violations, Comparable inconsistency, incorrect use of Optional
- Stream API: inefficient stream chains, side effects in map/filter, parallel stream misuse
- Thread safety: non-thread-safe types shared across threads, missing volatile for double-checked locking
- Reflection and unsafe casts without proper error handling
- Missing @Override annotations on overridden methods

Report format:
**[SEVERITY]** `file:line` - Title
Description of the Java-specific issue.
> Suggestion: How to fix it with idiomatic Java.""",
    2: """\
You are a Java architect performing a thorough language-specific review.

Read the full files and examine class hierarchies, dependency injection configuration, and framework usage. Use code graph tools to trace inheritance and call chains.

Analyze all items from the standard review plus:
- Design pattern violations (broken Singleton, misused Builder, unnecessary Factory)
- Spring/Jakarta EE issues: wrong scope annotations, missing @Transactional, incorrect bean lifecycle
- JPA/Hibernate: N+1 lazy loading, missing fetch joins, incorrect cascade types, entity equality
- Generics: raw types, unchecked casts, type erasure pitfalls
- Module system: split packages, missing exports, reflection access issues
- Records/sealed classes: missed opportunities, incorrect usage
- Virtual threads (Java 21+): pinning issues, blocking in virtual threads
- GC pressure: excessive allocation in hot paths, finalizer usage, weak/soft reference misuse
- Annotation processing: missing annotations that frameworks depend on
- Test anti-patterns: testing implementation not behavior, brittle assertions

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed analysis with Java-specific context and framework implications.
> Suggestion: Idiomatic fix with code example.""",
}


class JavaSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "java"

    @property
    def description(self) -> str:
        return "Java-specific review: null safety, concurrency, resource leaks, framework patterns"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        java_files = [f for f in context.pr.files if f.path.endswith(JAVA_EXTENSIONS)]
        if not java_files:
            return ""

        pr = context.pr
        prompt = "Review this PR for Java-specific issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Java Files\n"
        for f in java_files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full Java files for context. Check class hierarchies, "
            "exception handling, resource management, and concurrency patterns. "
            "Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        if not context.graph_client:
            return {}

        extra: dict[str, Any] = {}
        hierarchy_info = []

        for fc in context.pr.files:
            if not fc.path.endswith(JAVA_EXTENSIONS) or fc.status == "removed":
                continue
            try:
                results = context.graph_client.search(
                    fc.path, element_type="CLASS", limit=5
                )
                for elem in results[:3]:
                    eid = elem.get("id", "")
                    if not eid:
                        continue
                    hierarchy = context.graph_client.get_hierarchy(eid)
                    if hierarchy:
                        hierarchy_info.append(
                            {
                                "class": elem.get("qualifiedName", elem.get("name", "")),
                                "file": fc.path,
                                "hierarchy": hierarchy,
                            }
                        )
            except Exception:
                log.debug("Graph query failed for %s", fc.path, exc_info=True)

        if hierarchy_info:
            extra["class_hierarchies"] = hierarchy_info
        return extra


SkillRegistry.register(JavaSkill())
