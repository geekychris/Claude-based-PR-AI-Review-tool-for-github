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
    ensure_jar,
    generate_graph_config,
    generate_mcp_config,
    start_graph_server,
    stop_graph_server,
    wait_for_indexing,
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
    log.info("=" * 50)
    log.info("SKILL: %s — starting", skill.name)
    log.info("=" * 50)

    # Pre-analysis via code_graph_search
    log.info("[%s] Step 1: Running graph pre-analysis...", skill.name)
    extra_context = skill.pre_analyze(context)
    if extra_context:
        log.info(
            "[%s] Graph pre-analysis returned %d context keys: %s",
            skill.name,
            len(extra_context),
            ", ".join(f"{k}({len(v) if isinstance(v, (list, dict)) else 1})" for k, v in extra_context.items()),
        )
    else:
        log.info("[%s] Graph pre-analysis returned no extra context", skill.name)

    # Build prompts
    log.info("[%s] Step 2: Building prompts...", skill.name)
    system_prompt = build_system_prompt(skill, context)
    review_prompt = build_review_prompt(skill, context, extra_context)

    if not review_prompt:
        log.info("[%s] Skipped — build_review_prompt returned empty (no matching files)", skill.name)
        return SkillResult(skill_name=skill.name, summary="Skipped — not applicable")

    log.info(
        "[%s] Prompt size: system=%d chars, review=%d chars",
        skill.name,
        len(system_prompt),
        len(review_prompt),
    )

    # Invoke Claude
    budget = skill.max_budget_usd() or context.config.claude.max_budget_usd
    log.info(
        "[%s] Step 3: Invoking Claude (model=%s, max_turns=%d, budget=$%.2f, tools=%s)",
        skill.name,
        context.config.claude.model,
        context.config.claude.max_turns,
        budget,
        ",".join(skill.allowed_tools()),
    )
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
        log.error("[%s] Claude invocation FAILED: %s", skill.name, e)
        return SkillResult(
            skill_name=skill.name,
            summary=f"Error: {e}",
            raw_output=str(e),
        )

    log.info(
        "[%s] Claude responded: %d chars, session=%s, cost=$%.4f",
        skill.name,
        len(result.result),
        result.session_id[:12] if result.session_id else "n/a",
        result.cost_usd,
    )

    # Parse findings from Claude's response
    log.info("[%s] Step 4: Parsing findings...", skill.name)
    findings = skill.parse_findings(result.result)
    log.info(
        "[%s] Parsed %d finding(s): %s",
        skill.name,
        len(findings),
        ", ".join(f"{f.severity.value}:{f.file}:{f.line_start}" for f in findings[:10]),
    )

    # Extract summary (last paragraph or ## Summary section)
    summary = ""
    if "## Summary" in result.result:
        summary = result.result.split("## Summary")[-1].strip()

    log.info("[%s] COMPLETE — %d findings", skill.name, len(findings))
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

    # Step 3: Start code_graph_search, index the PR branch
    graph_client = None
    graph_process = None
    graph_config_path = None
    mcp_config_path = None
    use_graph = config.graph.enabled and not no_graph
    repo_id = f"{pr_data.owner}_{pr_data.repo}"

    if use_graph:
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as progress:
            task = progress.add_task("Initializing code_graph_search...", total=None)

            log.info("=" * 60)
            log.info("CODE_GRAPH_SEARCH: Initializing")
            log.info("  Host: %s", config.graph.host)
            log.info("  Auto-start: %s", config.graph.auto_start)
            log.info("  JAR path: %s", config.graph.jar_path or "(auto-detect)")
            log.info("  Source dir: %s", config.graph.code_graph_search_dir or "(not set)")
            log.info("  MCP mode: %s", config.graph.mcp_mode)
            log.info("  Target repo: %s at %s", repo_id, repo_dir)
            log.info("  PR branch: %s", pr_data.head_branch)
            log.info("=" * 60)

            jar_path = config.graph.jar_path

            # Resolve JAR — auto-detect from source dir if needed
            if config.graph.auto_start and not jar_path and config.graph.code_graph_search_dir:
                progress.update(task, description="Building code_graph_search...")
                try:
                    jar = ensure_jar(config.graph.code_graph_search_dir)
                    jar_path = str(jar)
                    log.info("JAR resolved: %s", jar_path)
                except Exception as e:
                    log.error("Failed to build/find code_graph_search JAR: %s", e)
                    console.print(f"[yellow]code_graph_search JAR not available: {e}[/yellow]")
                    use_graph = False

            if use_graph and config.graph.auto_start and jar_path:
                # Generate branch-aware config and start server
                progress.update(task, description="Starting code_graph_search server...")

                graph_config_path = str(generate_graph_config(
                    repo_path=str(repo_dir),
                    repo_id=repo_id,
                    repo_name=f"{pr_data.owner}/{pr_data.repo}",
                    branch=pr_data.head_branch,
                ))

                try:
                    log_file = str(Path(config.repo_checkout_dir) / "code_graph_search.log")
                    graph_process = start_graph_server(jar_path, graph_config_path, log_file=log_file)
                    console.print(f"  code_graph_search server log: {log_file}")
                except Exception as e:
                    log.error("Failed to start code_graph_search: %s", e)
                    console.print(f"[yellow]code_graph_search failed to start: {e}[/yellow]")
                    use_graph = False

            if use_graph and config.graph.auto_start and graph_process:
                # Wait for server to be ready
                progress.update(task, description="Waiting for code_graph_search to start...")
                log.info("Waiting for server to be ready (timeout=%ds)...", config.graph.startup_timeout_seconds)
                ready = wait_for_ready(config.graph.host, config.graph.startup_timeout_seconds)

                if not ready:
                    log.warning("code_graph_search did not start within timeout")
                    console.print("[yellow]code_graph_search failed to start, continuing without it[/yellow]")
                    stop_graph_server(graph_process)
                    graph_process = None
                    use_graph = False
                else:
                    log.info("Server is up — waiting for initial indexing...")

            elif use_graph and not config.graph.auto_start:
                # External server mode — check health
                log.info("Checking external code_graph_search at %s...", config.graph.host)
                client = GraphClient(config.graph.host)
                if not client.healthy():
                    log.warning("code_graph_search not reachable at %s", config.graph.host)
                    console.print(f"[yellow]code_graph_search not reachable at {config.graph.host}, continuing without it[/yellow]")
                    use_graph = False
                else:
                    log.info("External code_graph_search is healthy")
                client.close()

            # Index the PR branch content
            if use_graph:
                graph_client = GraphClient(config.graph.host)

                if config.graph.auto_start and graph_process:
                    # Server was started with config pointing at the repo —
                    # it indexes on startup. Wait for indexing to complete.
                    progress.update(
                        task,
                        description=f"Indexing {pr_data.head_branch} branch ({len(pr_data.files)} changed files)...",
                    )
                    indexed = wait_for_indexing(
                        graph_client,
                        repo_id,
                        timeout=config.graph.index_timeout_seconds,
                    )
                    if indexed:
                        console.print(f"  code_graph_search indexed branch [cyan]{pr_data.head_branch}[/cyan]")
                    else:
                        log.warning("Indexing may be incomplete — continuing with partial graph data")
                        console.print("[yellow]  code_graph_search indexing may be incomplete[/yellow]")
                else:
                    # External server — register/reindex the repo
                    progress.update(task, description="Registering repo for indexing...")
                    log.info("Registering/reindexing repo in external server: id=%s, path=%s", repo_id, repo_dir)
                    try:
                        existing = graph_client.list_repos()
                        existing_ids = [r.get("id", "") for r in existing]
                        log.info("Existing repos in graph: %s", existing_ids)

                        if repo_id in existing_ids:
                            log.info("Repo %s already indexed — triggering reindex for branch %s", repo_id, pr_data.head_branch)
                            graph_client.reindex_repo(repo_id)
                        else:
                            log.info("Adding repo %s at %s (branch: %s)", repo_id, repo_dir, pr_data.head_branch)
                            graph_client.add_repo(
                                repo_id=repo_id,
                                name=f"{pr_data.owner}/{pr_data.repo}",
                                path=str(repo_dir),
                            )

                        progress.update(task, description="Waiting for indexing...")
                        indexed = wait_for_indexing(
                            graph_client,
                            repo_id,
                            timeout=config.graph.index_timeout_seconds,
                        )
                        if indexed:
                            console.print(f"  code_graph_search indexed branch [cyan]{pr_data.head_branch}[/cyan]")
                        else:
                            log.warning("Indexing may be incomplete")
                    except Exception:
                        log.warning("Failed to register/reindex repo in code_graph_search", exc_info=True)

                progress.update(task, description="code_graph_search ready")
                log.info("CODE_GRAPH_SEARCH: Ready for queries (repo=%s, branch=%s)", repo_id, pr_data.head_branch)

                # Generate MCP config if mcp_mode is enabled
                if config.graph.mcp_mode and jar_path and graph_config_path:
                    mcp_config_path = str(generate_mcp_config(jar_path, graph_config_path))

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
