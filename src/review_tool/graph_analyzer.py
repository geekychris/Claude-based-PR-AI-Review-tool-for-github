"""Deep graph analysis using code_graph_search, driven by diff-parsed symbols.

This module provides targeted graph queries based on the actual symbols
changed in a PR, rather than broad file-path searches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from review_tool.diff_parser import ChangedSymbol
from review_tool.graph_client import GraphClient

log = logging.getLogger(__name__)


@dataclass
class ResolvedSymbol:
    """A changed symbol resolved to a code_graph_search element."""

    symbol: ChangedSymbol
    element_id: str
    qualified_name: str
    element_type: str
    element: dict[str, Any]


@dataclass
class GraphAnalysis:
    """Complete graph analysis results for a set of changed symbols."""

    resolved_symbols: list[ResolvedSymbol] = field(default_factory=list)
    callers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    callees: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hierarchies: dict[str, dict[str, Any]] = field(default_factory=dict)
    children: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    parents: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_prompt_context(self) -> dict[str, Any]:
        """Convert to a dict suitable for injecting into Claude prompts."""
        context: dict[str, Any] = {}

        if self.resolved_symbols:
            context["resolved_symbols"] = [
                {
                    "name": rs.symbol.name,
                    "qualified_name": rs.qualified_name,
                    "type": rs.element_type,
                    "file": rs.symbol.file,
                    "line": rs.symbol.line,
                }
                for rs in self.resolved_symbols
            ]

        if self.callers:
            context["callers"] = {
                name: [
                    {
                        "name": c.get("qualifiedName", c.get("name", "")),
                        "file": c.get("filePath", ""),
                        "line": c.get("lineStart", 0),
                        "type": c.get("elementType", ""),
                    }
                    for c in callers
                ]
                for name, callers in self.callers.items()
            }

        if self.callees:
            context["callees"] = {
                name: [
                    {
                        "name": c.get("qualifiedName", c.get("name", "")),
                        "file": c.get("filePath", ""),
                        "line": c.get("lineStart", 0),
                        "type": c.get("elementType", ""),
                    }
                    for c in callees
                ]
                for name, callees in self.callees.items()
            }

        if self.hierarchies:
            context["type_hierarchies"] = self.hierarchies

        if self.children:
            context["type_members"] = {
                name: [
                    {
                        "name": c.get("name", ""),
                        "type": c.get("elementType", ""),
                        "line": c.get("lineStart", 0),
                    }
                    for c in members
                ]
                for name, members in self.children.items()
            }

        if self.parents:
            context["parent_types"] = {
                name: {
                    "name": p.get("qualifiedName", p.get("name", "")),
                    "file": p.get("filePath", ""),
                    "type": p.get("elementType", ""),
                }
                for name, p in self.parents.items()
            }

        return context


def resolve_symbols(
    client: GraphClient,
    symbols: list[ChangedSymbol],
    repo_id: str,
) -> list[ResolvedSymbol]:
    """Resolve changed symbols to code_graph_search element IDs.

    Uses name-based search to find the graph elements corresponding
    to the symbols extracted from the diff.
    """
    resolved: list[ResolvedSymbol] = []

    for sym in symbols:
        log.debug("Resolving symbol: %s (%s) in %s", sym.name, sym.kind, sym.file)
        try:
            # Search by name, filtered to the specific file
            results = client.search_by_name(sym.name, repo=repo_id, limit=10)

            # Find the best match — same file path takes priority
            best: dict[str, Any] | None = None
            for r in results:
                r_path = r.get("filePath", "")
                if r_path.endswith(sym.file) or sym.file.endswith(r_path):
                    best = r
                    break
            if not best and results:
                # Fall back to first result
                best = results[0]

            if best:
                rs = ResolvedSymbol(
                    symbol=sym,
                    element_id=best.get("id", ""),
                    qualified_name=best.get("qualifiedName", best.get("name", sym.name)),
                    element_type=best.get("elementType", ""),
                    element=best,
                )
                resolved.append(rs)
                log.info(
                    "  Resolved: %s -> %s [%s] (id=%s)",
                    sym.name,
                    rs.qualified_name,
                    rs.element_type,
                    rs.element_id[:12],
                )
            else:
                log.info("  Could not resolve: %s in %s (no results)", sym.name, sym.file)

        except Exception:
            log.warning("  Failed to resolve symbol: %s", sym.name, exc_info=True)

    log.info(
        "Resolved %d/%d symbols in code_graph_search",
        len(resolved),
        len(symbols),
    )
    return resolved


def analyze_callers(
    client: GraphClient,
    resolved: list[ResolvedSymbol],
    max_callers: int = 15,
) -> dict[str, list[dict[str, Any]]]:
    """Find all callers of the resolved symbols."""
    callers: dict[str, list[dict[str, Any]]] = {}

    for rs in resolved:
        if rs.element_type not in ("METHOD", "FUNCTION", "CONSTRUCTOR"):
            continue
        try:
            result = client.get_callers(rs.element_id)
            if result:
                callers[rs.qualified_name] = result[:max_callers]
                log.info(
                    "  Callers of %s: %d found — %s",
                    rs.qualified_name,
                    len(result),
                    ", ".join(c.get("name", "?") for c in result[:5]),
                )
            else:
                log.debug("  No callers found for %s", rs.qualified_name)
        except Exception:
            log.warning("  Failed to get callers for %s", rs.qualified_name, exc_info=True)

    return callers


def analyze_callees(
    client: GraphClient,
    resolved: list[ResolvedSymbol],
    max_callees: int = 15,
) -> dict[str, list[dict[str, Any]]]:
    """Find all callees of the resolved symbols."""
    callees: dict[str, list[dict[str, Any]]] = {}

    for rs in resolved:
        if rs.element_type not in ("METHOD", "FUNCTION", "CONSTRUCTOR"):
            continue
        try:
            result = client.get_callees(rs.element_id)
            if result:
                callees[rs.qualified_name] = result[:max_callees]
                log.info(
                    "  Callees of %s: %d found — %s",
                    rs.qualified_name,
                    len(result),
                    ", ".join(c.get("name", "?") for c in result[:5]),
                )
            else:
                log.debug("  No callees found for %s", rs.qualified_name)
        except Exception:
            log.warning("  Failed to get callees for %s", rs.qualified_name, exc_info=True)

    return callees


def analyze_hierarchies(
    client: GraphClient,
    resolved: list[ResolvedSymbol],
) -> dict[str, dict[str, Any]]:
    """Get type hierarchies for resolved class/interface/trait symbols."""
    hierarchies: dict[str, dict[str, Any]] = {}

    type_kinds = ("CLASS", "INTERFACE", "TRAIT", "STRUCT", "ENUM")
    for rs in resolved:
        if rs.element_type not in type_kinds:
            continue
        try:
            result = client.get_hierarchy(rs.element_id)
            if result:
                hierarchies[rs.qualified_name] = result
                log.info("  Hierarchy for %s: %s", rs.qualified_name, _summarize_hierarchy(result))
            else:
                log.debug("  No hierarchy for %s", rs.qualified_name)
        except Exception:
            log.warning("  Failed to get hierarchy for %s", rs.qualified_name, exc_info=True)

    return hierarchies


def analyze_children(
    client: GraphClient,
    resolved: list[ResolvedSymbol],
    max_children: int = 25,
) -> dict[str, list[dict[str, Any]]]:
    """Get members/children of resolved type symbols."""
    children: dict[str, list[dict[str, Any]]] = {}

    type_kinds = ("CLASS", "INTERFACE", "TRAIT", "STRUCT", "ENUM", "MODULE")
    for rs in resolved:
        if rs.element_type not in type_kinds:
            continue
        try:
            result = client.get_children(rs.element_id)
            if result:
                children[rs.qualified_name] = result[:max_children]
                log.info(
                    "  Members of %s: %d — %s",
                    rs.qualified_name,
                    len(result),
                    ", ".join(c.get("name", "?") for c in result[:5]),
                )
            else:
                log.debug("  No children for %s", rs.qualified_name)
        except Exception:
            log.warning("  Failed to get children for %s", rs.qualified_name, exc_info=True)

    return children


def analyze_parents(
    client: GraphClient,
    resolved: list[ResolvedSymbol],
) -> dict[str, dict[str, Any]]:
    """Get parent types/modules of resolved symbols."""
    parents: dict[str, dict[str, Any]] = {}

    for rs in resolved:
        if rs.element_type in ("METHOD", "FUNCTION", "FIELD"):
            try:
                result = client.get_parent(rs.element_id)
                if result:
                    parents[rs.qualified_name] = result
                    log.info(
                        "  Parent of %s: %s [%s]",
                        rs.qualified_name,
                        result.get("qualifiedName", result.get("name", "?")),
                        result.get("elementType", "?"),
                    )
            except Exception:
                log.warning("  Failed to get parent for %s", rs.qualified_name, exc_info=True)

    return parents


def run_full_analysis(
    client: GraphClient,
    symbols: list[ChangedSymbol],
    repo_id: str,
    *,
    include_callers: bool = True,
    include_callees: bool = True,
    include_hierarchies: bool = True,
    include_children: bool = True,
    include_parents: bool = True,
) -> GraphAnalysis:
    """Run a complete graph analysis on a set of changed symbols.

    This is the main entry point for skills to use.
    """
    log.info("=" * 60)
    log.info("GRAPH ANALYSIS: Starting for %d symbols", len(symbols))
    log.info("=" * 60)

    # Step 1: Resolve symbols to graph elements
    log.info("--- Step 1: Resolving symbols ---")
    resolved = resolve_symbols(client, symbols, repo_id)

    if not resolved:
        log.info("No symbols resolved — skipping graph analysis")
        return GraphAnalysis()

    analysis = GraphAnalysis(resolved_symbols=resolved)

    # Step 2: Analyze relationships
    if include_callers:
        log.info("--- Step 2a: Finding callers ---")
        analysis.callers = analyze_callers(client, resolved)

    if include_callees:
        log.info("--- Step 2b: Finding callees ---")
        analysis.callees = analyze_callees(client, resolved)

    if include_hierarchies:
        log.info("--- Step 2c: Finding type hierarchies ---")
        analysis.hierarchies = analyze_hierarchies(client, resolved)

    if include_children:
        log.info("--- Step 2d: Finding type members ---")
        analysis.children = analyze_children(client, resolved)

    if include_parents:
        log.info("--- Step 2e: Finding parent types ---")
        analysis.parents = analyze_parents(client, resolved)

    # Summary
    log.info("=" * 60)
    log.info(
        "GRAPH ANALYSIS COMPLETE: %d symbols resolved, %d callers, %d callees, "
        "%d hierarchies, %d type members, %d parents",
        len(analysis.resolved_symbols),
        sum(len(v) for v in analysis.callers.values()),
        sum(len(v) for v in analysis.callees.values()),
        len(analysis.hierarchies),
        sum(len(v) for v in analysis.children.values()),
        len(analysis.parents),
    )
    log.info("=" * 60)

    return analysis


def _summarize_hierarchy(h: dict[str, Any]) -> str:
    """Create a short summary of a hierarchy dict for logging."""
    parts = []
    if "superclass" in h:
        parts.append(f"extends {h['superclass']}")
    if "interfaces" in h:
        ifaces = h["interfaces"]
        if isinstance(ifaces, list):
            parts.append(f"implements {', '.join(str(i) for i in ifaces[:3])}")
    if "subclasses" in h:
        subs = h["subclasses"]
        if isinstance(subs, list):
            parts.append(f"{len(subs)} subclass(es)")
    return ", ".join(parts) if parts else "(no hierarchy info)"
