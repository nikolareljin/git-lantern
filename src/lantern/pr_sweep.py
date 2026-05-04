"""Discovery logic for the `lantern pr sweep` command.

Finds open PRs with unresolved review threads across personal GitHub repos,
applying filters for forks, archived repos, and forge-mind frozen projects.
"""

import json
import shutil
import subprocess
from typing import Dict, List, Optional, Set, Tuple

from . import github
from . import forge_client as _forge_client


def gh_authenticated_user() -> Optional[str]:
    """Return the login of the currently authenticated GitHub user via `gh`, or None."""
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [gh, "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def list_owner_repos(owner: str) -> Optional[List[Dict]]:
    """List repos for *owner* via `gh repo list`, returning raw repo metadata.

    Each entry has keys: ``full_name``, ``fork``, ``archived``.
    Returns None if `gh` is unavailable or the call fails.
    """
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [
                gh, "repo", "list", owner,
                "--json", "nameWithOwner,isFork,isArchived",
                "--limit", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "[]")
        if not isinstance(data, list):
            return None
        return [
            {
                "full_name": r.get("nameWithOwner", ""),
                "fork": bool(r.get("isFork")),
                "archived": bool(r.get("isArchived")),
            }
            for r in data
            if isinstance(r, dict)
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def fetch_pr_unresolved_thread_count(owner: str, repo: str, pr_number: int) -> int:
    """Return the number of unresolved review threads on *pr_number* via GraphQL.

    Returns -1 when the count cannot be determined (gh unavailable, API error).
    """
    gh = shutil.which("gh")
    if not gh:
        return -1

    query = (
        "query($owner:String!,$repo:String!,$pr:Int!){"
        "repository(owner:$owner,name:$repo){"
        "pullRequest(number:$pr){"
        "reviewThreads(first:100){nodes{isResolved}}}}}"
    )
    try:
        proc = subprocess.run(
            [
                gh, "api", "graphql",
                "-f", f"query={query}",
                "-F", f"owner={owner}",
                "-F", f"repo={repo}",
                "-F", f"pr={pr_number}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return -1
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return -1

    nodes = (
        (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )
    return sum(1 for t in nodes if isinstance(t, dict) and not t.get("isResolved"))


def discover_eligible_prs(
    owner: str,
    token: Optional[str],
    forge_url: str,
    skip_forks: bool,
    skip_frozen: bool,
    repos_filter: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[str]]:
    """Discover open PRs with unresolved review threads across *owner*'s repos.

    Args:
        owner:        GitHub username whose repos to scan.
        token:        GitHub personal access token (optional, falls back to gh auth).
        forge_url:    Base URL of a forge-mind instance for the frozen-repo check.
        skip_forks:   Exclude forked repositories.
        skip_frozen:  Exclude repos that forge-mind considers frozen/archived.
        repos_filter: If given, restrict scan to these ``owner/repo`` full names.

    Returns:
        A ``(jobs, warnings)`` tuple where *jobs* is a list of dicts::

            {"repo": "owner/repo", "pr": <int>, "title": <str>,
             "url": <str>, "unresolved_threads": <int>}

        and *warnings* is a list of human-readable warning strings.
    """
    warnings: List[str] = []

    # 1. List repos owned by the target account.
    raw_repos = list_owner_repos(owner)
    if raw_repos is None:
        # Fallback: GitHub REST API via forge.fetch_repos.
        from . import forge as _forge
        try:
            api_repos = _forge.fetch_repos(
                "github",
                owner,
                token,
                not skip_forks,
                "",
                include_user=True,
            )
        except ValueError as exc:
            return [], [f"Failed to list repos for '{owner}': {exc}"]
        raw_repos = [
            {"full_name": r.get("name", ""), "fork": False, "archived": False}
            for r in api_repos
        ]

    # 2. Apply fork and archived filters.
    candidates = [
        r for r in raw_repos
        if (not skip_forks or not r.get("fork")) and not r.get("archived")
    ]

    # 3. Restrict to caller-specified repos when provided.
    if repos_filter:
        lower_filter: Set[str] = {s.lower() for s in repos_filter}
        candidates = [r for r in candidates if r["full_name"].lower() in lower_filter]

    # 4. Frozen check via forge-mind.
    frozen: Set[str] = set()
    if skip_frozen and forge_url:
        try:
            frozen = _forge_client.fetch_frozen_repos(forge_url)
        except Exception:
            warnings.append(
                f"Warning: could not reach forge-mind at {forge_url!r}; skipping frozen filter."
            )

    eligible_repos = [r for r in candidates if r["full_name"].lower() not in frozen]

    # 5. For each eligible repo, fetch open PRs and count unresolved review threads.
    jobs: List[Dict] = []
    for repo_entry in eligible_repos:
        full_name = repo_entry["full_name"]
        parts = full_name.split("/", 1)
        if len(parts) != 2:
            continue
        repo_owner, repo_name = parts

        open_prs = github.fetch_open_pull_requests(repo_owner, repo_name, token)
        for pr in open_prs:
            pr_number = pr.get("number")
            if pr_number is None:
                continue
            unresolved = fetch_pr_unresolved_thread_count(repo_owner, repo_name, pr_number)
            # Include when count is unknown (-1) or positive; skip confirmed-zero.
            if unresolved != 0:
                jobs.append(
                    {
                        "repo": full_name,
                        "pr": pr_number,
                        "title": pr.get("title", ""),
                        "url": pr.get("html_url", ""),
                        "unresolved_threads": unresolved,
                    }
                )

    return jobs, warnings
