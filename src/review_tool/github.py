"""GitHub interaction via the gh CLI."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from review_tool.config import AppConfig
from review_tool.models import FileChange, PRData

log = logging.getLogger(__name__)


def _run_gh(args: list[str], *, token: str | None = None, check: bool = True) -> str:
    """Run a gh CLI command and return stdout."""
    env = None
    if token:
        import os

        env = {**os.environ, "GH_TOKEN": token}

    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}... failed: {result.stderr.strip()}")
    return result.stdout


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract owner, repo, and PR number from a GitHub PR URL."""
    # Handles: https://github.com/owner/repo/pull/123
    import re

    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Invalid PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_pr(url: str, config: AppConfig) -> PRData:
    """Fetch PR metadata and diff from GitHub."""
    owner, repo, number = parse_pr_url(url)
    token = config.github.resolved_token()

    # Fetch metadata
    fields = "number,title,body,headRefName,baseRefName,author,labels,files,additions,deletions"
    raw = _run_gh(
        ["pr", "view", url, "--json", fields],
        token=token,
    )
    data = json.loads(raw)

    # Fetch diff
    diff_text = _run_gh(["pr", "diff", url], token=token)

    # Parse files
    files = []
    for f in data.get("files", []):
        files.append(
            FileChange(
                path=f.get("path", ""),
                status=f.get("status", "modified"),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch", ""),
            )
        )

    labels = [lbl.get("name", "") for lbl in data.get("labels", [])]
    author = data.get("author", {})
    author_login = author.get("login", "") if isinstance(author, dict) else str(author)

    return PRData(
        url=url,
        owner=owner,
        repo=repo,
        number=number,
        title=data.get("title", ""),
        body=data.get("body", ""),
        author=author_login,
        base_branch=data.get("baseRefName", "main"),
        head_branch=data.get("headRefName", ""),
        files=files,
        diff_text=diff_text,
        labels=labels,
    )


def checkout_pr(url: str, config: AppConfig) -> Path:
    """Clone and checkout the PR branch locally. Returns repo directory."""
    owner, repo, number = parse_pr_url(url)
    token = config.github.resolved_token()

    checkout_dir = Path(config.repo_checkout_dir)
    checkout_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = checkout_dir / f"{owner}_{repo}"

    if not repo_dir.exists():
        _run_gh(
            ["repo", "clone", f"{owner}/{repo}", str(repo_dir)],
            token=token,
        )

    # Checkout the PR branch
    subprocess.run(
        ["gh", "pr", "checkout", str(number)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, **({"GH_TOKEN": token} if token else {})},
        timeout=120,
        check=True,
    )

    return repo_dir


def post_review(
    url: str,
    body: str,
    *,
    event: str = "COMMENT",
    config: AppConfig,
) -> None:
    """Post a review to the PR. event: COMMENT, REQUEST_CHANGES, or APPROVE."""
    token = config.github.resolved_token()
    owner, repo, number = parse_pr_url(url)

    # gh pr review uses --approve, --request-changes, or --comment flags
    flag_map = {
        "APPROVE": "--approve",
        "REQUEST_CHANGES": "--request-changes",
        "COMMENT": "--comment",
    }
    flag = flag_map.get(event, "--comment")

    # Write body to temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        _run_gh(
            ["pr", "review", url, flag, "--body-file", body_file],
            token=token,
        )
        log.info("Posted %s review to %s", event, url)
    except RuntimeError as e:
        if "request changes on your own" in str(e).lower() and event == "REQUEST_CHANGES":
            # GitHub doesn't allow REQUEST_CHANGES on your own PR — fall back to COMMENT
            log.warning("Cannot request changes on own PR, falling back to COMMENT")
            _run_gh(
                ["pr", "review", url, "--comment", "--body-file", body_file],
                token=token,
            )
            log.info("Posted COMMENT review to %s (fallback from REQUEST_CHANGES)", url)
        else:
            raise
    finally:
        Path(body_file).unlink(missing_ok=True)


def post_inline_comment(
    url: str,
    *,
    path: str,
    line: int,
    body: str,
    side: str = "RIGHT",
    start_line: int | None = None,
    start_side: str | None = None,
    config: AppConfig,
) -> None:
    """Post an inline review comment on a specific file/line in the PR diff.

    For multi-line comments (needed for GitHub suggestion blocks), pass
    start_line and start_side to define the range [start_line, line].
    """
    owner, repo, number = parse_pr_url(url)
    token = config.github.resolved_token()

    # Get the head commit SHA (required by the API)
    pr_raw = _run_gh(["pr", "view", url, "--json", "headRefOid"], token=token)
    commit_id = json.loads(pr_raw).get("headRefOid", "")

    payload_dict: dict = {
        "body": body,
        "path": path,
        "line": line,
        "side": side,
        "commit_id": commit_id,
    }

    # Multi-line range for suggestion blocks
    if start_line is not None and start_line != line:
        payload_dict["start_line"] = start_line
        payload_dict["start_side"] = start_side or side

    payload = json.dumps(payload_dict)

    env = None
    if token:
        import os
        env = {**os.environ, "GH_TOKEN": token}

    log.info(
        "Posting inline comment: %s:%s%s (%d chars, %s)",
        path,
        f"{start_line}-{line}" if start_line else str(line),
        " [has suggestion]" if "```suggestion" in body else "",
        len(body),
        side,
    )

    result = subprocess.run(
        [
            "gh", "api",
            f"repos/{owner}/{repo}/pulls/{number}/comments",
            "--method", "POST",
            "--input", "-",
        ],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to post inline comment: {result.stderr.strip()}")


def post_commit_status(
    url: str,
    *,
    state: str,
    description: str,
    context: str = "ai-review / review-tool",
    target_url: str = "",
    config: AppConfig,
) -> None:
    """Post a commit status to the head commit of the PR.

    Args:
        url: GitHub PR URL.
        state: One of 'pending', 'success', 'failure', 'error'.
        description: Short description (max ~140 chars).
        context: Identifies this status check (shown in PR checks list).
        target_url: Optional link to detailed results.
        config: App config for auth.
    """
    owner, repo, number = parse_pr_url(url)
    token = config.github.resolved_token()

    # Get the head commit SHA
    pr_raw = _run_gh(["pr", "view", url, "--json", "headRefOid"], token=token)
    sha = json.loads(pr_raw).get("headRefOid", "")
    if not sha:
        log.warning("Could not get head commit SHA for %s", url)
        return

    payload_dict: dict = {
        "state": state,
        "description": description[:140],
        "context": context,
    }
    if target_url:
        payload_dict["target_url"] = target_url

    payload = json.dumps(payload_dict)

    env = None
    if token:
        import os
        env = {**os.environ, "GH_TOKEN": token}

    log.info(
        "Posting commit status: %s/%s@%s state=%s context='%s' desc='%s'",
        owner, repo, sha[:8], state, context, description[:50],
    )

    result = subprocess.run(
        [
            "gh", "api",
            f"repos/{owner}/{repo}/statuses/{sha}",
            "--method", "POST",
            "--input", "-",
        ],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        log.warning("Failed to post commit status: %s", result.stderr.strip())
