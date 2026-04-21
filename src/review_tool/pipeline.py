"""Review pipeline — orchestrates the full PR review flow."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from review_tool import claude as claude_cli
from review_tool.config import AppConfig
from review_tool.formatter import (
    aggregate_results,
    determine_review_event,
    format_inline_comments,
    format_review_body,
)
from review_tool.github import checkout_pr, fetch_pr, post_inline_comment, post_review
from review_tool.graph_client import GraphClient
from review_tool.graph_lifecycle import (
    generate_graph_config,
    generate_mcp_config,
    start_graph_server,
    stop_graph_server,
    wait_for_ready,
)
from review_tool.models import PRData, ReviewContext, SkillResult
from review_tool.prompt_builder import build_review_prompt, build_system_prompt
from review_tool.skills import BaseSkill, SkillRegistry

log = logging.getLogger(__name__)
console = Console()


def _load_guidance(config: AppConfig) -> str:
    """Load custom guidance from file if configured."""
    if config.guidance_file:
        path = Path(config.guidance_file)
        if path.exists():
            return path.read_text()
        log.warning("Guidance file not found: %s", path)
    return ""


def _run_skill(
    skill: BaseSkill,
    context: ReviewContext,
    *,
    mcp_config_path: str | None = None,
) -> SkillResult:
    """Execute a single skill: pre-analyze, build prompt, invoke Claude, parse."""
    log.info("Running skill: %s", skill.name)

    # Pre-analysis via code_graph_search
    extra_context = skill.pre_analyze(context)

    # Build prompts
    system_prompt = build_system_prompt(skill, context)
    review_prompt = build_review_prompt(skill, context, extra_context)

    if not review_prompt:
        log.info("Skill %s skipped (empty prompt)", skill.name)
        return SkillResult(skill_name=skill.name, summary="Skipped — not applicable")

    # Invoke Claude
    budget = skill.max_budget_usd() or context.config.claude.max_budget_usd
    try:
        result = claude_cli.invoke(
            review_prompt,
            system_prompt=system_prompt,
            allowed_tools=skill.allowed_tools(),
            add_dirs=[context.repo_dir],
            mcp_config=mcp_config_path,
            model=context.config.claude.model,
            max_turns=context.config.claude.max_turns,
            max_budget_usd=budget,
            permission_mode=context.config.claude.permission_mode,
        )
    except claude_cli.ClaudeError as e:
        log.error("Skill %s failed: %s", skill.name, e)
        return SkillResult(
            skill_name=skill.name,
            summary=f"Error: {e}",
            raw_output=str(e),
        )

    # Parse findings from Claude's response
    findings = skill.parse_findings(result.result)
    log.info("Skill %s found %d issue(s)", skill.name, len(findings))

    # Extract summary (last paragraph or ## Summary section)
    summary = ""
    if "## Summary" in result.result:
        summary = result.result.split("## Summary")[-1].strip()

    return SkillResult(
        skill_name=skill.name,
        findings=findings,
        summary=summary,
        raw_output=result.result,
    )


def run_review(
    pr_url: str,
    config: AppConfig,
    *,
    skill_names: list[str] | None = None,
    verbosity: int | None = None,
    dry_run: bool = False,
    no_graph: bool = False,
    guidance_file: str | None = None,
    output_file: str | None = None,
) -> list[SkillResult]:
    """Execute the full review pipeline.

    Args:
        pr_url: GitHub PR URL to review.
        config: Application configuration.
        skill_names: Skills to run (None = use config defaults).
        verbosity: Override verbosity level.
        dry_run: Print review without posting to GitHub.
        no_graph: Skip code_graph_search integration.
        guidance_file: Path to extra guidance markdown.
        output_file: Write review to file instead of posting.

    Returns:
        List of SkillResult from each skill.
    """
    if verbosity is not None:
        config.verbosity = verbosity
    if guidance_file:
        config.guidance_file = guidance_file

    # Load guidance
    guidance = _load_guidance(config)

    # Resolve skills
    if skill_names:
        skills = SkillRegistry.get_enabled(skill_names)
    else:
        skills = SkillRegistry.get_enabled(config.skills.enabled)

    # Load custom skills
    if config.skills.custom_skills_dir:
        SkillRegistry.load_custom_skills(config.skills.custom_skills_dir)
        if skill_names:
            skills = SkillRegistry.get_enabled(skill_names)

    if not skills:
        console.print("[red]No skills enabled or found.[/red]")
        return []

    console.print(f"[bold]Reviewing:[/bold] {pr_url}")
    console.print(f"[bold]Skills:[/bold] {', '.join(s.name for s in skills)}")

    # Step 1: Fetch PR data
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Fetching PR data...", total=None)
        pr_data = fetch_pr(pr_url, config)
        progress.update(task, description=f"PR #{pr_data.number}: {pr_data.title}")

    console.print(
        f"  {len(pr_data.files)} file(s) changed, "
        f"+{sum(f.additions for f in pr_data.files)}/"
        f"-{sum(f.deletions for f in pr_data.files)} lines"
    )

    # Step 2: Checkout repository
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Checking out PR branch...", total=None)
        repo_dir = checkout_pr(pr_url, config)
        progress.update(task, description=f"Repo at {repo_dir}")

    # Step 3: Start code_graph_search (if enabled)
    graph_client = None
    graph_process = None
    mcp_config_path = None
    use_graph = config.graph.enabled and not no_graph

    if use_graph:
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as progress:
            task = progress.add_task("Starting code_graph_search...", total=None)

            if config.graph.jar_path:
                graph_process = start_graph_server(config.graph, str(repo_dir))
                ready = wait_for_ready(
                    config.graph.host, config.graph.startup_timeout_seconds
                )
                if not ready:
                    console.print("[yellow]code_graph_search failed to start, continuing without it[/yellow]")
                    use_graph = False
            else:
                # Assume it's already running
                client = GraphClient(config.graph.host)
                if not client.healthy():
                    console.print("[yellow]code_graph_search not reachable, continuing without it[/yellow]")
                    use_graph = False
                client.close()

            if use_graph:
                graph_client = GraphClient(config.graph.host)

                # Register/reindex the repo so code_graph_search has fresh content
                progress.update(task, description="Indexing repository...")
                repo_id = f"{pr_data.owner}_{pr_data.repo}"
                try:
                    existing = graph_client.list_repos()
                    existing_ids = [r.get("id", "") for r in existing]
                    if repo_id in existing_ids:
                        graph_client.reindex_repo(repo_id)
                        log.info("Triggered reindex for repo %s", repo_id)
                    else:
                        graph_client.add_repo(
                            repo_id=repo_id,
                            name=f"{pr_data.owner}/{pr_data.repo}",
                            path=str(repo_dir),
                        )
                        log.info("Added repo %s at %s", repo_id, repo_dir)
                    # Wait for indexing to settle
                    import time
                    time.sleep(5)
                except Exception:
                    log.warning("Failed to register/reindex repo in code_graph_search", exc_info=True)

                progress.update(task, description="code_graph_search ready")

                # Generate MCP config if mcp_mode is enabled
                if config.graph.mcp_mode and config.graph.jar_path:
                    cfg_path = config.graph.config_path or str(
                        generate_graph_config(str(repo_dir))
                    )
                    mcp_config_path = str(
                        generate_mcp_config(config.graph.jar_path, cfg_path)
                    )

    # Step 4: Build context and run skills
    context = ReviewContext(
        pr=pr_data,
        repo_dir=str(repo_dir),
        graph_client=graph_client,
        config=config,
        verbosity=config.verbosity,
        guidance=guidance,
    )

    results: list[SkillResult] = []
    for skill in skills:
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as progress:
            task = progress.add_task(
                f"Running {skill.name} review...", total=None
            )
            result = _run_skill(skill, context, mcp_config_path=mcp_config_path)
            results.append(result)
            count = len(result.findings)
            progress.update(
                task,
                description=f"{skill.name}: {count} finding(s)",
            )

    # Step 5: Cleanup graph
    if graph_client:
        graph_client.close()
    if graph_process:
        stop_graph_server(graph_process)

    # Step 6: Format and output
    all_findings = aggregate_results(results)
    review_body = format_review_body(results, verbosity=config.verbosity)
    event = determine_review_event(all_findings)

    console.print(f"\n[bold]Review complete:[/bold] {len(all_findings)} finding(s)")
    for sev_name in ["critical", "high", "medium", "low", "info"]:
        count = sum(1 for f in all_findings if f.severity.value == sev_name)
        if count:
            console.print(f"  {sev_name}: {count}")
    console.print(f"  Recommendation: {event}")

    if output_file:
        Path(output_file).write_text(review_body)
        console.print(f"\nReview written to {output_file}")
    elif dry_run:
        console.print("\n--- Review Body ---\n")
        console.print(review_body)
    else:
        # Post to GitHub
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as progress:
            task = progress.add_task("Posting review to GitHub...", total=None)
            post_review(pr_url, review_body, event=event, config=config)

            # Post inline comments for specific findings
            inline_comments = format_inline_comments(results)
            for comment in inline_comments:
                try:
                    post_inline_comment(
                        pr_url,
                        path=comment["path"],
                        line=comment["line"],
                        body=comment["body"],
                        config=config,
                    )
                except Exception:
                    log.debug("Failed to post inline comment: %s", comment, exc_info=True)

            progress.update(task, description="Review posted")

        console.print(f"\n[green]Review posted to {pr_url}[/green]")

    return results
