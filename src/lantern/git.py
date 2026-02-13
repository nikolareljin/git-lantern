import os
import subprocess
from typing import Dict, Optional, Tuple


def run_git(repo_path: str, args: list) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout.strip()


def is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def fetch(repo_path: str) -> None:
    subprocess.run(
        ["git", "-C", repo_path, "fetch", "--prune"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def get_branch(repo_path: str) -> str:
    branch = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    return branch or "detached"


def get_upstream(repo_path: str) -> Optional[str]:
    upstream = run_git(
        repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    return upstream or None


def has_in_progress_operation(repo_path: str) -> bool:
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        return False
    markers = (
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
    )
    for marker in markers:
        if os.path.exists(os.path.join(git_dir, marker)):
            return True
    if os.path.isdir(os.path.join(git_dir, "rebase-merge")):
        return True
    if os.path.isdir(os.path.join(git_dir, "rebase-apply")):
        return True
    return False


def is_clean(repo_path: str) -> bool:
    # For sync eligibility, do not treat local uncommitted/untracked files as dirty.
    # Only block repositories that are in the middle of a Git operation.
    return not has_in_progress_operation(repo_path)


def count_ahead_behind(repo_path: str, left: str, right: str) -> Tuple[int, int]:
    counts = run_git(repo_path, ["rev-list", "--left-right", "--count", f"{left}...{right}"])
    if not counts:
        return 0, 0
    parts = counts.split()
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def get_origin_url(repo_path: str) -> Optional[str]:
    url = run_git(repo_path, ["remote", "get-url", "origin"])
    return url or None


def get_default_branch_ref(repo_path: str) -> Optional[str]:
    refs = get_default_branch_refs(repo_path)
    if "origin" in refs:
        return refs["origin"]
    for ref in refs.values():
        return ref
    return None


def get_default_branch_refs(repo_path: str) -> Dict[str, str]:
    remotes_raw = run_git(repo_path, ["remote"])
    remotes = [r.strip() for r in remotes_raw.splitlines() if r.strip()]
    refs: Dict[str, str] = {}

    for remote in remotes:
        head_ref = run_git(
            repo_path,
            ["symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"],
        )
        if head_ref:
            refs[remote] = head_ref
            continue
        for candidate in (f"{remote}/main", f"{remote}/master"):
            ref = run_git(repo_path, ["rev-parse", "--verify", candidate])
            if ref:
                refs[remote] = candidate
                break
    return refs


def repo_status(repo_path: str) -> Dict[str, Optional[str]]:
    branch = get_branch(repo_path)
    upstream = get_upstream(repo_path)
    upstream_ahead = None
    upstream_behind = None
    if upstream:
        ahead, behind = count_ahead_behind(repo_path, "HEAD", upstream)
        upstream_ahead = str(ahead)
        upstream_behind = str(behind)

    default_refs = get_default_branch_refs(repo_path)
    main_ref = get_default_branch_ref(repo_path)
    main_ahead = None
    main_behind = None
    if main_ref:
        ahead, behind = count_ahead_behind(repo_path, "HEAD", main_ref)
        main_ahead = str(ahead)
        main_behind = str(behind)

    return {
        "branch": branch,
        "upstream": upstream,
        "upstream_ahead": upstream_ahead,
        "upstream_behind": upstream_behind,
        "main_ref": main_ref,
        "main_ahead": main_ahead,
        "main_behind": main_behind,
        "default_refs": ", ".join(default_refs.values()) if default_refs else None,
    }
