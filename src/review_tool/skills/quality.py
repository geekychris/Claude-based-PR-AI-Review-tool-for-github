"""Code quality skill — reviews style, maintainability, and design."""

from __future__ import annotations

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

SYSTEM_PROMPTS = {
    0: """\
You are a code reviewer focused on quality. Report only significant design issues. Be extremely concise.""",
    1: """\
You are a code reviewer focused on code quality and maintainability.

Look for:
- Unclear or misleading naming
- Functions that are too long or do too many things
- Duplicated logic that should be extracted
- Missing or misleading error messages
- Inconsistent patterns relative to the rest of the codebase
- Dead code or unreachable branches
- Poor abstractions (leaky, wrong level)
- Missing edge case handling

Focus on actionable, meaningful issues — not style nitpicks.

Report format:
**[SEVERITY]** `file:line` - Title
Why this is a quality concern and how it affects maintainability.
> Suggestion: How to improve.""",
    2: """\
You are a senior engineer performing a thorough code quality review.

Read the full files and surrounding code to understand conventions and patterns in the codebase. Compare the PR's approach to existing patterns.

Analyze:
- Naming clarity and consistency with codebase conventions
- Function/method complexity and single responsibility
- Duplication across the changed files and existing code
- Error handling completeness and consistency
- API design (is the interface intuitive, consistent, minimal?)
- Testability of the new code
- Documentation accuracy (do comments match the code?)
- Dependency management (unnecessary dependencies, circular imports)
- Breaking changes or backward compatibility

Report format:
**[SEVERITY]** `file:line-endline` - Title
Detailed analysis including comparison to existing patterns.
> Suggestion: Specific improvement with rationale.""",
}


class QualitySkill(BaseSkill):
    @property
    def name(self) -> str:
        return "quality"

    @property
    def description(self) -> str:
        return "Review code quality, maintainability, naming, complexity, and design patterns"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        pr = context.pr
        prompt = "Review this PR for code quality and maintainability.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Files\n"
        for f in pr.files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nRead the full files to understand existing patterns and conventions. "
            "Focus on meaningful quality issues, not style nitpicks. "
            "Report findings in the specified format."
        )
        return prompt


SkillRegistry.register(QualitySkill())
