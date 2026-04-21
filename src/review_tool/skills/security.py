"""Security review skill — finds vulnerabilities and security anti-patterns."""

from __future__ import annotations

import logging
from typing import Any

from review_tool.models import ReviewContext
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    0: """\
You are a security reviewer. Report only critical and high severity vulnerabilities. Be extremely concise.""",
    1: """\
You are a security-focused code reviewer. Analyze changes for vulnerabilities aligned with OWASP Top 10 and CWE.

Look for:
- Injection flaws (SQL, command, LDAP, XSS, template injection)
- Broken authentication and session management
- Sensitive data exposure (secrets in code, insecure storage, logging PII)
- Broken access control (missing auth checks, privilege escalation paths)
- Security misconfiguration (debug modes, default credentials, permissive CORS)
- Insecure deserialization
- Insufficient input validation at trust boundaries
- Cryptographic weaknesses (weak algorithms, hardcoded keys, predictable randomness)
- Dependency vulnerabilities (known CVEs in imported packages)
- Path traversal and file inclusion

For each finding, report:
**[SEVERITY]** `file:line` - Title
Description of the vulnerability and its potential impact.
> Suggestion: How to remediate.""",
    2: """\
You are an application security expert performing a thorough security audit of a pull request.

Go beyond the diff — read full files, trace data flows from sources to sinks, check authentication/authorization middleware, and examine trust boundaries.

Analyze for:
- OWASP Top 10 vulnerabilities with full attack scenario
- CWE-classified weaknesses
- Data flow from user input to sensitive operations (trace the full path)
- Authentication and authorization bypass opportunities
- Secrets, tokens, or credentials in code or config
- Insecure cryptographic practices
- Server-Side Request Forgery (SSRF)
- Race conditions that could lead to security issues
- Dependency chain vulnerabilities
- Security regression (removed security controls)

For each finding, provide:
**[SEVERITY]** `file:line-endline` - Title (CWE-XXX)
Detailed description of the vulnerability, attack scenario, and impact.
> Suggestion: Specific remediation with code example.""",
}


class SecuritySkill(BaseSkill):
    @property
    def name(self) -> str:
        return "security"

    @property
    def description(self) -> str:
        return "Find security vulnerabilities, injection flaws, auth issues, and data exposure"

    def system_prompt(self, verbosity: int = 1) -> str:
        level = min(verbosity, max(SYSTEM_PROMPTS.keys()))
        return SYSTEM_PROMPTS[level]

    def build_review_prompt(self, context: ReviewContext) -> str:
        pr = context.pr
        prompt = "Review this PR for security vulnerabilities.\n\n"
        prompt += f"**PR:** {pr.title}\n"
        prompt += f"**Author:** {pr.author}\n"
        if pr.body:
            prompt += f"**Description:** {pr.body[:500]}\n"
        prompt += "\n## Changed Files\n"
        for f in pr.files:
            prompt += f"- `{f.path}` ({f.status}, +{f.additions}/-{f.deletions})\n"
        prompt += f"\n## Diff\n```diff\n{pr.diff_text}\n```\n"
        prompt += (
            "\nExamine full file contents for security context. "
            "Trace data flows from user input to sensitive operations. "
            "Check for missing authentication/authorization. "
            "Report findings in the specified format."
        )
        return prompt

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        """Use graph search to trace data flows through changed code."""
        if not context.graph_client:
            return {}

        from review_tool.diff_parser import extract_changed_symbols
        from review_tool.graph_analyzer import run_full_analysis

        log.info("[security] Running graph pre-analysis for security review")
        symbols = extract_changed_symbols(context.pr.diff_text)
        if not symbols:
            log.info("[security] No symbols extracted from diff — skipping graph analysis")
            return {}

        repo_id = f"{context.pr.owner}_{context.pr.repo}"
        analysis = run_full_analysis(
            context.graph_client,
            symbols,
            repo_id,
            include_callers=True,     # Who calls this? Trace input sources.
            include_callees=True,     # What does this call? Trace to sinks (DB, exec, file).
            include_hierarchies=True, # Is this an auth middleware, controller, etc?
            include_children=False,
            include_parents=True,     # What class owns this? Is it a controller/handler?
        )
        return analysis.to_prompt_context()


SkillRegistry.register(SecuritySkill())
