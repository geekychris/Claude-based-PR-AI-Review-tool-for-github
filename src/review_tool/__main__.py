"""CLI entry point for review-tool."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from review_tool.config import AppConfig, generate_default_config, load_config

app = typer.Typer(
    name="review-tool",
    help="AI-driven code review powered by Claude Code",
    no_args_is_help=True,
)
skills_app = typer.Typer(help="Manage review skills")
config_app = typer.Typer(help="Manage configuration")
app.add_typer(skills_app, name="skills")
app.add_typer(config_app, name="config")

console = Console()


def _setup_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG, 3: logging.DEBUG}
    logging.basicConfig(
        level=level.get(verbosity, logging.DEBUG),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def review(
    pr_url: str = typer.Argument(help="GitHub PR URL to review"),
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config JSON file"
    ),
    skills: Optional[str] = typer.Option(
        None, "--skills", "-s", help="Comma-separated skill names to run"
    ),
    verbosity: int = typer.Option(
        None, "--verbosity", "-v", help="Verbosity: 0=summary, 1=normal, 2=detailed, 3=debug"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print review without posting"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override Claude model"),
    max_budget: Optional[float] = typer.Option(
        None, "--max-budget", help="Override max budget USD per skill"
    ),
    no_graph: bool = typer.Option(False, "--no-graph", help="Skip code_graph_search"),
    guidance: Optional[str] = typer.Option(
        None, "--guidance", "-g", help="Path to extra guidance markdown file"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Write review to file instead of posting"
    ),
) -> None:
    """Review a GitHub pull request using AI-powered analysis."""
    config = load_config(config_path)

    if model:
        config.claude.model = model
    if max_budget is not None:
        config.claude.max_budget_usd = max_budget
    if verbosity is not None:
        _setup_logging(verbosity)
    else:
        verbosity = config.verbosity
        _setup_logging(verbosity)

    skill_names = [s.strip() for s in skills.split(",")] if skills else None

    from review_tool.pipeline import run_review

    results = run_review(
        pr_url,
        config,
        skill_names=skill_names,
        verbosity=verbosity,
        dry_run=dry_run,
        no_graph=no_graph,
        guidance_file=guidance,
        output_file=output,
    )

    # Exit with non-zero if critical/high findings
    from review_tool.models import Severity

    has_critical = any(
        f.severity in (Severity.CRITICAL, Severity.HIGH)
        for r in results
        for f in r.findings
    )
    if has_critical:
        raise SystemExit(1)


# -- Skills subcommands -------------------------------------------------------


@skills_app.command("list")
def skills_list(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """List available review skills."""
    # Trigger skill registration by importing
    from review_tool.skills import SkillRegistry

    config = load_config(config_path)

    # Load custom skills if configured
    if config.skills.custom_skills_dir:
        SkillRegistry.load_custom_skills(config.skills.custom_skills_dir)

    table = Table(title="Available Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Description")

    for name, skill in sorted(SkillRegistry.all_skills().items()):
        enabled = "yes" if name in config.skills.enabled else "no"
        table.add_row(name, enabled, skill.description)

    console.print(table)


@skills_app.command("show")
def skills_show(
    name: str = typer.Argument(help="Skill name to show details for"),
) -> None:
    """Show details for a specific skill."""
    from review_tool.skills import SkillRegistry

    skill = SkillRegistry.get(name)
    if not skill:
        console.print(f"[red]Skill '{name}' not found[/red]")
        raise SystemExit(1)

    console.print(f"[bold cyan]{skill.name}[/bold cyan]")
    console.print(f"[dim]{skill.description}[/dim]\n")
    console.print("[bold]System Prompt (verbosity=1):[/bold]")
    console.print(skill.system_prompt(1))
    console.print(f"\n[bold]Allowed Tools:[/bold] {', '.join(skill.allowed_tools())}")


# -- Config subcommands --------------------------------------------------------


@config_app.command("init")
def config_init(
    path: str = typer.Option("review_tool.json", "--path", "-p"),
) -> None:
    """Generate a default configuration file."""
    out = generate_default_config(path)
    console.print(f"[green]Config written to {out}[/green]")


@config_app.command("check")
def config_check(
    path: str = typer.Option("review_tool.json", "--config", "-c"),
) -> None:
    """Validate a configuration file."""
    try:
        config = load_config(path)
        console.print("[green]Configuration is valid[/green]")

        # Show summary
        console.print(f"  Model: {config.claude.model}")
        console.print(f"  Budget: ${config.claude.max_budget_usd}/skill")
        console.print(f"  Graph: {'enabled' if config.graph.enabled else 'disabled'}")
        console.print(f"  Skills: {', '.join(config.skills.enabled)}")
        console.print(f"  Verbosity: {config.verbosity}")
    except Exception as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        raise SystemExit(1)


if __name__ == "__main__":
    app()
