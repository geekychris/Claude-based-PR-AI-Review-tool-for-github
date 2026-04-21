"""Data models for the review pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review_tool.config import AppConfig
    from review_tool.graph_client import GraphClient


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }[self]


@dataclass
class Finding:
    """A single review finding tied to a location in the code."""

    file: str
    line_start: int
    severity: Severity
    category: str  # skill name that produced this
    title: str
    description: str
    line_end: int | None = None
    suggestion: str | None = None

    def sort_key(self) -> tuple[int, str, int]:
        return (self.severity.rank, self.file, self.line_start)


@dataclass
class SkillResult:
    """Output from a single skill's review pass."""

    skill_name: str
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    raw_output: str = ""


@dataclass
class FileChange:
    """A file changed in the PR."""

    path: str
    status: str  # added, modified, removed, renamed
    additions: int = 0
    deletions: int = 0
    patch: str = ""


@dataclass
class PRData:
    """Parsed PR metadata and content."""

    url: str
    owner: str
    repo: str
    number: int
    title: str
    body: str
    author: str
    base_branch: str
    head_branch: str
    files: list[FileChange] = field(default_factory=list)
    diff_text: str = ""
    labels: list[str] = field(default_factory=list)


@dataclass
class ReviewContext:
    """Full context passed to each skill during review."""

    pr: PRData
    repo_dir: str
    graph_client: GraphClient | None
    config: AppConfig
    verbosity: int
    guidance: str = ""
