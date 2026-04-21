"""Extensible review skills system."""

from __future__ import annotations

import importlib
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from review_tool.models import Finding, ReviewContext, SkillResult

log = logging.getLogger(__name__)


class BaseSkill(ABC):
    """Abstract base class for review skills.

    To create a custom skill:
    1. Subclass BaseSkill
    2. Implement the required properties and methods
    3. Call SkillRegistry.register(YourSkill()) at module level
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this skill (e.g., 'defects', 'security')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this skill reviews."""
        ...

    @abstractmethod
    def system_prompt(self, verbosity: int = 1) -> str:
        """Return the system prompt fragment for Claude.

        The verbosity level controls how detailed the review should be:
        0 = summary only, 1 = normal, 2 = detailed, 3 = exhaustive
        """
        ...

    @abstractmethod
    def build_review_prompt(self, context: ReviewContext) -> str:
        """Build the full review prompt given PR context.

        Return empty string to skip this skill for this PR.
        """
        ...

    def allowed_tools(self) -> list[str]:
        """Which tools Claude can use during this skill's review."""
        return ["Bash", "Read", "Grep", "Glob"]

    def pre_analyze(self, context: ReviewContext) -> dict[str, Any]:
        """Optional: run code_graph_search queries before Claude invocation.

        Returns extra context dict to be injected into the prompt by
        prompt_builder.
        """
        return {}

    def parse_findings(self, raw_text: str) -> list[Finding]:
        """Parse Claude's output text into structured findings.

        Default implementation looks for markdown-formatted findings.
        Override for custom parsing.
        """
        return _parse_findings_from_markdown(raw_text, self.name)

    def max_budget_usd(self) -> float | None:
        """Override to set a per-skill budget. None uses global default."""
        return None


class SkillRegistry:
    """Central registry for review skills."""

    _skills: dict[str, BaseSkill] = {}

    @classmethod
    def register(cls, skill: BaseSkill) -> None:
        cls._skills[skill.name] = skill
        log.debug("Registered skill: %s", skill.name)

    @classmethod
    def get(cls, name: str) -> BaseSkill | None:
        return cls._skills.get(name)

    @classmethod
    def all_skills(cls) -> dict[str, BaseSkill]:
        return dict(cls._skills)

    @classmethod
    def get_enabled(cls, enabled_names: list[str]) -> list[BaseSkill]:
        skills = []
        for name in enabled_names:
            skill = cls._skills.get(name)
            if skill:
                skills.append(skill)
            else:
                log.warning("Skill '%s' not found in registry", name)
        return skills

    @classmethod
    def load_custom_skills(cls, directory: str) -> None:
        """Import all .py files from a directory, each expected to register skills."""
        skill_dir = Path(directory)
        if not skill_dir.is_dir():
            log.warning("Custom skills directory not found: %s", directory)
            return

        sys.path.insert(0, str(skill_dir))
        for py_file in skill_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            module_name = py_file.stem
            try:
                importlib.import_module(module_name)
                log.info("Loaded custom skill module: %s", module_name)
            except Exception:
                log.exception("Failed to load custom skill: %s", py_file)
        sys.path.pop(0)


def _parse_findings_from_markdown(text: str, category: str) -> list[Finding]:
    """Best-effort parser for Claude's markdown-formatted findings.

    Expects findings in a format like:
    **[SEVERITY]** `file:line` - Title
    Description text...
    > Suggestion: ...
    """
    from review_tool.models import Severity

    findings: list[Finding] = []
    import re

    # Pattern: **[SEVERITY]** `file:line` - Title
    pattern = re.compile(
        r"\*\*\[(\w+)\]\*\*\s*`([^:]+):(\d+)(?:-(\d+))?`\s*[-–—]\s*(.+)"
    )

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = pattern.match(lines[i].strip())
        if m:
            sev_str = m.group(1).lower()
            try:
                severity = Severity(sev_str)
            except ValueError:
                severity = Severity.MEDIUM

            file_path = m.group(2)
            line_start = int(m.group(3))
            line_end = int(m.group(4)) if m.group(4) else None
            title = m.group(5).strip()

            # Collect description lines until next finding or section
            desc_lines = []
            suggestion = None
            i += 1
            while i < len(lines):
                line = lines[i].strip()
                if pattern.match(line) or line.startswith("## ") or line.startswith("### "):
                    break
                if line.startswith("> Suggestion:") or line.startswith("> Fix:"):
                    suggestion = line.split(":", 1)[1].strip()
                elif line:
                    desc_lines.append(line)
                i += 1

            findings.append(
                Finding(
                    file=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    severity=severity,
                    category=category,
                    title=title,
                    description="\n".join(desc_lines),
                    suggestion=suggestion,
                )
            )
        else:
            i += 1

    return findings


# Import built-in skills to auto-register them
from review_tool.skills import defects as _defects  # noqa: F401, E402
from review_tool.skills import lang_go as _lang_go  # noqa: F401, E402
from review_tool.skills import lang_java as _lang_java  # noqa: F401, E402
from review_tool.skills import lang_rust as _lang_rust  # noqa: F401, E402
from review_tool.skills import lang_typescript as _lang_typescript  # noqa: F401, E402
from review_tool.skills import performance as _performance  # noqa: F401, E402
from review_tool.skills import quality as _quality  # noqa: F401, E402
from review_tool.skills import security as _security  # noqa: F401, E402
