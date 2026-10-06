import os
import shlex
import subprocess
from typing import Dict, Optional, Tuple, TypedDict


class RepoStatus(TypedDict):
    branch: Optional[str]
    upstream: Optional[str]
    upstream_inferred: bool
    upstream_ahead: Optional[str]
    upstream_behind: Optional[str]
    main_ref: Optional[str]
    main_ahead: Optional[str]
    main_behind: Optional[str]
    default_refs: Optional[str]


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
        ["git", "-C", repo_path, "fetch", "--all", "--prune"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def get_branch(repo_path: str) -> Optional[str]:
    branch = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        return None
    return branch


def get_upstream(repo_path: str) -> Optional[str]:
    upstream = run_git(
        repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    return upstream or None


def has_in_progress_operation(repo_path: str) -> bool:
    resolved_git_dir = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--git-dir"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.strip()
    if not resolved_git_dir:
        return False
    git_dir = resolved_git_dir if os.path.isabs(resolved_git_dir) else os.path.join(repo_path, resolved_git_dir)
    if not os.path.isdir(git_dir):
        return False
    markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
    )
    for marker in markers:
        if os.path.exists(os.path.join(git_dir, marker)):
            return True
    # REBASE_HEAD can remain after a completed rebase; the state directories
    # below identify an active rebase.
    if os.path.isdir(os.path.join(git_dir, "rebase-merge")):
        return True
    if os.path.isdir(os.path.join(git_dir, "rebase-apply")):
        return True
    return False


def is_operation_free(repo_path: str) -> bool:
    """Return True when no merge/rebase/cherry-pick/revert/bisect is in progress."""
    return not has_in_progress_operation(repo_path)


def is_clean(repo_path: str) -> bool:
    """Return True when the working tree has no uncommitted or untracked changes."""
    status_output = run_git(repo_path, ["status", "--porcelain"])
    return status_output == ""


def get_working_tree_state(repo_path: str) -> Dict[str, object]:
    """Classify whether a repo is clean, untracked-only, or has tracked changes."""
    result = subprocess.run(
        ["git", "-C", repo_path, "status", "--porcelain"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    status_output = result.stdout.strip()
    if result.returncode != 0:
        error_parts = ["git status failed"]
        stderr_text = str(result.stderr or "").strip()
        if stderr_text:
            error_parts.append(stderr_text)
        error_parts.append(f"exit={result.returncode}")
        return {
            "status_ok": False,
            "is_clean": False,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": None,
            "error": "; ".join(error_parts),
        }
    if not status_output:
        return {
            "status_ok": True,
            "is_clean": True,
            "has_untracked": False,
            "has_tracked_changes": False,
            "allows_checkout_latest": True,
            "error": "",
        }

    has_untracked = False
    has_tracked_changes = False
    for raw_line in status_output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("??"):
            has_untracked = True
            continue
        has_tracked_changes = True

    return {
        "status_ok": True,
        "is_clean": False,
        "has_untracked": has_untracked,
        "has_tracked_changes": has_tracked_changes,
        "allows_checkout_latest": not has_tracked_changes,
        "error": "",
    }


def sync_submodules(repo_path: str) -> Dict[str, str]:
    """Restore recorded gitlinks without forcing over edits or staged pointers."""
    if not os.path.isfile(os.path.join(repo_path, ".gitmodules")):
        return {"status": "none"}

    def run(args: list) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", repo_path, *args], capture_output=True, text=True, check=False,
        )

    if has_in_progress_operation(repo_path):
        return {"status": "skip-dirty", "detail": "Git operation in progress"}
    config_state = run(["status", "--porcelain", "--", ".gitmodules"])
    if config_state.returncode:
        return {"status": "fail", "detail": config_state.stderr.strip()}
    if config_state.stdout.strip():
        return {"status": "skip-dirty", "detail": "Local .gitmodules changes"}
    index = run(["ls-files", "--stage", "-z"])
    if index.returncode:
        return {"status": "fail", "detail": index.stderr.strip()}
    paths = []
    for entry in index.stdout.split("\0"):
        if entry.startswith("160000 "):
            metadata, path = entry.split("\t", 1)
            if not metadata.endswith(" 0"):
                return {"status": "skip-dirty", "detail": "Conflicted submodule pointer"}
            paths.append(path)
    if not paths:
        return {"status": "none"}
    staged = run(["diff", "--cached", "--quiet", "--", *paths])
    if staged.returncode:
        return {"status": "skip-dirty" if staged.returncode == 1 else "fail",
                "detail": "Staged submodule pointer changes"}
    # Inspect initialized modules recursively, including nested worktrees.
    modules = run(["submodule", "foreach", "--quiet", "--recursive", "git rev-parse --show-toplevel"])
    if modules.returncode:
        return {"status": "fail", "detail": modules.stderr.strip()}
    for module_path in modules.stdout.splitlines():
        state = get_working_tree_state(module_path)
        if not state.get("status_ok"):
            return {"status": "fail", "detail": str(state.get("error") or module_path)}
        if not state.get("is_clean") or has_in_progress_operation(module_path):
            return {"status": "skip-dirty", "detail": f"Local submodule changes: {module_path}"}
    for args in (["submodule", "sync", "--recursive"],
                 ["submodule", "update", "--init", "--recursive", "--checkout"]):
        result = run(args)
        if result.returncode:
            return {"status": "fail", "detail": result.stderr.strip()}
    return {"status": "ok"}


def count_ahead_behind(repo_path: str, left: str, right: str) -> Tuple[int, int]:
    counts = run_git(repo_path, ["rev-list", "--left-right", "--count", f"{left}...{right}"])
    if not counts:
        return 0, 0
    parts = counts.split()
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def noninteractive_env() -> Dict[str, str]:
    """Environment for network git commands that must fail instead of prompting."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if env.get("GIT_SSH"):
        return env
    ssh_command = env.get("GIT_SSH_COMMAND") or run_git(".", ["config", "--get", "core.sshCommand"]) or "ssh"
    try:
        program = os.path.basename(shlex.split(ssh_command)[0]) if ssh_command.strip() else ""
    except ValueError:
        program = ""
    if program in {"ssh", "ssh.exe"}:
        # BatchMode stops passphrase, password and host key prompts from blocking.
        ssh_command = f"{ssh_command} -o BatchMode=yes -o ConnectTimeout=15"
    env["GIT_SSH_COMMAND"] = ssh_command
    return env


def _last_error_line(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    # The SSH line names the actual cause; git's own "fatal:" line after it is generic.
    for line in lines:
        if "permission denied" in line.lower() or "host key verification failed" in line.lower():
            return line
    for line in reversed(lines):
        if line.lower().startswith(("fatal:", "error:")):
            return line
    return lines[-1] if lines else ""


def check_remote_access(url: str, timeout: int = 30) -> Tuple[bool, str]:
    """Return whether the remote answers without prompting, plus git's error output."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--", url, "HEAD"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=noninteractive_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    return result.returncode == 0, (result.stderr or "").strip()


def clone_repo(url: str, dest: str) -> Tuple[bool, str]:
    """Clone without prompting; return success and git's error output."""
    result = subprocess.run(
        ["git", "clone", "--", url, dest],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=noninteractive_env(),
    )
    ok = result.returncode == 0
    return ok, "" if ok else _last_error_line(result.stderr or "")


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


def repo_status(repo_path: str) -> RepoStatus:
    branch = get_branch(repo_path)
    upstream = get_upstream(repo_path)
    upstream_ahead = None
    upstream_behind = None
    upstream_inferred = False
    if upstream:
        ahead, behind = count_ahead_behind(repo_path, "HEAD", upstream)
        upstream_ahead = str(ahead)
        upstream_behind = str(behind)
    elif branch:
        # No tracking branch — fall back to origin/<branch> if it exists so
        # repos without @{u} still show as behind-remote after a fetch.
        candidate = f"origin/{branch}"
        if run_git(repo_path, ["rev-parse", "--verify", candidate]):
            ahead, behind = count_ahead_behind(repo_path, "HEAD", candidate)
            upstream_ahead = str(ahead)
            upstream_behind = str(behind)
            upstream_inferred = True

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
        "upstream_inferred": upstream_inferred,
        "upstream_ahead": upstream_ahead,
        "upstream_behind": upstream_behind,
        "main_ref": main_ref,
        "main_ahead": main_ahead,
        "main_behind": main_behind,
        "default_refs": ", ".join(default_refs.values()) if default_refs else None,
    }
