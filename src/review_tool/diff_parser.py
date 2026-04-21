"""Parse unified diffs to extract changed symbols and locations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ChangedSymbol:
    """A symbol (function, class, method) that was modified in the diff."""

    name: str
    kind: str  # "function", "class", "method", "interface", "struct", "trait", "type"
    file: str
    line: int
    language: str = ""


@dataclass
class DiffHunk:
    """A single hunk from a unified diff."""

    file: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # the @@ line, often contains function context
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)


def parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    """Parse unified diff text into structured hunks."""
    hunks: list[DiffHunk] = []
    current_file = ""
    current_hunk: DiffHunk | None = None

    for line in diff_text.split("\n"):
        # Track file changes
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("--- a/"):
            continue

        # Parse hunk headers
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line)
        if m:
            current_hunk = DiffHunk(
                file=current_file,
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or "1"),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or "1"),
                header=m.group(5).strip(),
            )
            hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_hunk.added_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current_hunk.removed_lines.append(line[1:])

    log.info("Parsed %d diff hunks across files", len(hunks))
    return hunks


# Language-specific symbol extraction patterns
_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "java": [
        ("class", re.compile(r"(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)")),
        ("interface", re.compile(r"(?:public\s+)?interface\s+(\w+)")),
        ("method", re.compile(r"(?:public|private|protected)\s+(?:static\s+)?(?:[\w<>\[\]]+\s+)+(\w+)\s*\(")),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(\w+)\s*\(")),
        ("method", re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(")),
        ("struct", re.compile(r"^type\s+(\w+)\s+struct\b")),
        ("interface", re.compile(r"^type\s+(\w+)\s+interface\b")),
    ],
    "rust": [
        ("function", re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
        ("struct", re.compile(r"(?:pub\s+)?struct\s+(\w+)")),
        ("trait", re.compile(r"(?:pub\s+)?trait\s+(\w+)")),
        ("type", re.compile(r"(?:pub\s+)?enum\s+(\w+)")),
        ("type", re.compile(r"(?:pub\s+)?type\s+(\w+)")),
    ],
    "typescript": [
        ("function", re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
        ("class", re.compile(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")),
        ("interface", re.compile(r"(?:export\s+)?interface\s+(\w+)")),
        ("type", re.compile(r"(?:export\s+)?type\s+(\w+)\s*=")),
        ("method", re.compile(r"(?:async\s+)?(\w+)\s*\([^)]*\)\s*[:{]")),
    ],
    "python": [
        ("function", re.compile(r"(?:async\s+)?def\s+(\w+)\s*\(")),
        ("class", re.compile(r"class\s+(\w+)")),
    ],
}

# Map file extensions to language keys
_EXT_TO_LANG: dict[str, str] = {
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "typescript",  # close enough for symbol extraction
    ".jsx": "typescript",
    ".py": "python",
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    for ext, lang in _EXT_TO_LANG.items():
        if file_path.endswith(ext):
            return lang
    return ""


def extract_changed_symbols(diff_text: str) -> list[ChangedSymbol]:
    """Extract function/class/method names from changed lines in a diff.

    Parses both added and removed lines to find symbol definitions
    that were modified.
    """
    hunks = parse_diff_hunks(diff_text)
    symbols: list[ChangedSymbol] = []
    seen: set[tuple[str, str, str]] = set()  # (file, name, kind) dedup

    for hunk in hunks:
        lang = _detect_language(hunk.file)
        if not lang:
            continue

        patterns = _PATTERNS.get(lang, [])
        all_changed_lines = hunk.added_lines + hunk.removed_lines

        for line in all_changed_lines:
            for kind, pattern in patterns:
                m = pattern.search(line)
                if m:
                    name = m.group(1)
                    # Skip common false positives
                    if name in ("if", "for", "while", "return", "new", "var", "let", "const", "self", "this"):
                        continue
                    key = (hunk.file, name, kind)
                    if key not in seen:
                        seen.add(key)
                        symbols.append(
                            ChangedSymbol(
                                name=name,
                                kind=kind,
                                file=hunk.file,
                                line=hunk.new_start,
                                language=lang,
                            )
                        )

        # Also extract from hunk headers (e.g., @@ ... @@ void processData(...))
        if hunk.header:
            lang_patterns = _PATTERNS.get(lang, [])
            for kind, pattern in lang_patterns:
                m = pattern.search(hunk.header)
                if m:
                    name = m.group(1)
                    key = (hunk.file, name, "context")
                    if key not in seen:
                        seen.add(key)
                        symbols.append(
                            ChangedSymbol(
                                name=name,
                                kind="context",
                                file=hunk.file,
                                line=hunk.new_start,
                                language=lang,
                            )
                        )

    log.info(
        "Extracted %d changed symbols: %s",
        len(symbols),
        ", ".join(f"{s.name}({s.kind})" for s in symbols[:20]),
    )
    return symbols


def symbols_for_file(symbols: list[ChangedSymbol], file_path: str) -> list[ChangedSymbol]:
    """Filter symbols to those in a specific file."""
    return [s for s in symbols if s.file == file_path]


def symbols_for_language(symbols: list[ChangedSymbol], language: str) -> list[ChangedSymbol]:
    """Filter symbols to those in a specific language."""
    return [s for s in symbols if s.language == language]
