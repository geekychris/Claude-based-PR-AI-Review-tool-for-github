"""Format review findings into GitHub-compatible output."""

from __future__ import annotations

from collections import defaultdict

from review_tool.models import Finding, Severity, SkillResult

SEVERITY_ICONS = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "ℹ️",
}


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate findings based on file + line + title similarity."""
    seen: set[tuple[str, int, str]] = set()
    unique: list[Finding] = []
    for f in findings:
        key = (f.file, f.line_start, f.title.lower()[:50])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def aggregate_results(results: list[SkillResult]) -> list[Finding]:
    """Merge findings from all skills, deduplicate, and sort by severity."""
    all_findings: list[Finding] = []
    for r in results:
        all_findings.extend(r.findings)
    unique = deduplicate_findings(all_findings)
    unique.sort(key=lambda f: f.sort_key())
    return unique


def format_review_body(
    results: list[SkillResult],
    *,
    verbosity: int = 1,
) -> str:
    """Format all findings into a GitHub review body (markdown)."""
    findings = aggregate_results(results)

    lines = ["## AIdrian Code Review\n"]

    # Summary stats
    skill_names = [r.skill_name for r in results]
    total_files = len({f.file for f in findings})
    lines.append(
        f"Reviewed with skills: **{', '.join(skill_names)}** "
        f"| {len(findings)} finding(s) across {total_files} file(s)\n"
    )

    if not findings:
        lines.append("No issues found. The changes look good.\n")
        return "\n".join(lines)

    # Group by severity
    by_severity: dict[Severity, list[Finding]] = defaultdict(list)
    for f in findings:
        by_severity[f.severity].append(f)

    for sev in Severity:
        group = by_severity.get(sev)
        if not group:
            continue

        icon = SEVERITY_ICONS[sev]
        lines.append(f"### {icon} {sev.value.title()} ({len(group)})\n")

        for f in group:
            loc = f"`{f.file}:{f.line_start}"
            if f.line_end:
                loc += f"-{f.line_end}"
            loc += "`"

            lines.append(f"- {f.title} — {loc} *{f.category}*")
            if verbosity >= 1 and f.description:
                for desc_line in f.description.split("\n"):
                    lines.append(f"  {desc_line}")
            if verbosity >= 1 and f.suggestion:
                lines.append(f"  💡 {f.suggestion}")
            lines.append("")

    # Per-skill summaries (collapsed at lower verbosity)
    if verbosity >= 2:
        lines.append("---\n")
        lines.append("<details><summary>Skill Summaries</summary>\n")
        for r in results:
            if r.summary:
                lines.append(f"#### {r.skill_name}\n{r.summary}\n")
        lines.append("</details>\n")

    lines.append("---\n*Reviewed by AIdrian*")
    return "\n".join(lines)


def format_inline_comments(
    results: list[SkillResult],
) -> list[dict]:
    """Build a list of inline comment dicts for posting to GitHub.

    Each dict has: path, line, start_line (for multi-line), body, side.
    When a finding has a code_suggestion, the body includes a GitHub
    suggestion block that the PR author can accept with one click.
    """
    findings = aggregate_results(results)
    comments = []

    for f in findings:
        icon = SEVERITY_ICONS[f.severity]
        sev = f.severity.value.upper()

        # Clean, scannable format: icon + severity tag + title on one line
        body = f"{icon} `{sev}` {f.title} &nbsp;·&nbsp; *{f.category}*\n\n"
        body += f.description

        if f.code_suggestion is not None:
            # GitHub suggestion block — author can click "Apply suggestion"
            body += f"\n\n```suggestion\n{f.code_suggestion}\n```"
        elif f.suggestion:
            body += f"\n\n💡 {f.suggestion}"

        comment: dict = {
            "path": f.file,
            "line": f.line_end if f.line_end else f.line_start,
            "body": body,
            "side": "RIGHT",
        }

        # Multi-line range: GitHub API uses start_line + line for the range
        if f.line_end and f.line_end > f.line_start:
            comment["start_line"] = f.line_start
            comment["start_side"] = "RIGHT"

        comments.append(comment)

    return comments


def determine_review_event(findings: list[Finding]) -> str:
    """Decide whether to APPROVE, COMMENT, or REQUEST_CHANGES based on findings."""
    if not findings:
        return "APPROVE"

    severities = {f.severity for f in findings}
    if Severity.CRITICAL in severities:
        return "REQUEST_CHANGES"
    if Severity.HIGH in severities:
        return "REQUEST_CHANGES"
    return "COMMENT"
