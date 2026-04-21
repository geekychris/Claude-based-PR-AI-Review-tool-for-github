"""Rust-specific review skill."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

RUST_EXTENSIONS = (".rs",)

SYSTEM_PROMPTS = {
    0: """\
You are a Rust expert reviewer. Report only critical and high severity Rust-specific issues. Be extremely concise.""",
    1: """\
You are a senior Rust engineer performing a language-specific code review.

Look for Rust-specific issues:
- Unsafe code: unnecessary unsafe blocks, unsound unsafe implementations, missing safety docs
- Ownership/borrowing: unnecessary clones, overly broad lifetimes, fighting the borrow checker with RefCell/Rc when redesign is better
- Error handling: unwrap()/expect() in library code, missing error context (use anyhow/thiserror), error type design
- Concurrency: data races across unsafe boundaries, deadlock patterns with Mutex, missing Send/Sync bounds, async pitfalls
- Memory: large stack allocations (Box it), unnecessary allocations, Vec capacity hints missing in hot paths
- API design: missing #[must_use], non-exhaustive enums without #[non_exhaustive], breaking public API changes
- Trait misuse: incorrect impl ordering, missing derive macros, orphan rule violations
- Async: holding MutexGuard across .await, blocking in async context, missing cancellation safety
- Clippy-level issues: manual implementations of standard traits, redundant closures, needless borrows
- Panic paths: index out of bounds, integer overflow in release mode, slice panics
- Macro hygiene: unhygienic macro_rules, missing error messages in compile-time assertions
- Dependency concerns: feature flag conflicts, unsound dependencies

Report format:
**[SEVERITY]** `file:line` - Title
Description of the Rust-specific issue.
> Suggestion: Idiomatic Rust fix.""",
    2: """\
You are a Rust expert performing a thorough language-specific review.

Read full files and trace ownership chains, lifetime relationships, and trait implementations. Examine Cargo.toml for dependency concerns.

Analyze all items from the standard review plus:
- Soundness: can safe code trigger UB through this API? Are unsafe invariants documented and maintained?
- Lifetime design: could lifetime elision simplify signatures? Are lifetime bounds too restrictive or too loose?
- Zero-cost abstraction opportunities: could generics replace trait objects? Is dynamic dispatch necessary?
- Const generics and const fn opportunities
- Pin correctness for self-referential types and async
- FFI safety: correct repr, null pointer handling, panic across FFI boundary
- Allocator awareness: global vs custom allocators, allocation patterns in no_std
- MSRV implications of new feature usage
- Procedural macro correctness and error reporting
- Workspace and feature flag interactions

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed analysis with ownership/lifetime/soundness implications.
> Suggestion: Idiomatic fix with code example.""",
}


class RustSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "rust"

    @property
    def description(self) -> str:
        return "Rust-specific review: unsafe, ownership, lifetimes, async, error handling"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        rust_files = [
            f for f in context.pr.files
            if f.path.endswith(RUST_EXTENSIONS) or f.path == "Cargo.toml"
        ]
        if not rust_files:
            return ""

        pr = context.pr
        prompt = "Review this PR for Rust-specific issues.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Rust Files\n"
        for f in rust_files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full Rust source files. Check ownership patterns, "
            "unsafe blocks, error handling, trait implementations, and async usage. "
            "Also check Cargo.toml if changed. Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        if not context.graph_client:
            return {}

        from review_tool.diff_parser import extract_changed_symbols, symbols_for_language
        from review_tool.graph_analyzer import run_full_analysis

        log.info("[rust] Running graph pre-analysis for Rust review")
        all_symbols = extract_changed_symbols(context.pr.diff_text)
        symbols = symbols_for_language(all_symbols, "rust")
        if not symbols:
            log.info("[rust] No Rust symbols extracted from diff — skipping graph analysis")
            return {}

        repo_id = f"{context.pr.owner}_{context.pr.repo}"
        analysis = run_full_analysis(
            context.graph_client,
            symbols,
            repo_id,
            include_callers=True,     # Who calls changed functions?
            include_callees=True,     # What does this function call?
            include_hierarchies=True, # Trait implementations
            include_children=True,    # Struct fields, trait methods, enum variants
            include_parents=True,     # Enclosing module/impl block
        )
        return analysis.to_prompt_context()


SkillRegistry.register(RustSkill())
