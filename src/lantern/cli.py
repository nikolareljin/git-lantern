import argparse
from datetime import datetime, timezone
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Any, Dict, List, MutableMapping, Optional, Set, Tuple

try:
    import argcomplete
except ImportError:  # pragma: no cover - optional dependency in some environments
    argcomplete = None

from . import config as lantern_config
from . import forge
from . import git
from . import github
from .table import render_table

# Resolved path to the src/ directory for subprocess PYTHONPATH
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Common server presets for TUI setup
SERVER_PRESETS = {
    "github.com": {
        "provider": "github",
        "base_url": "https://api.github.com",
    },
    "gitlab.com": {
        "provider": "gitlab",
        "base_url": "https://gitlab.com/api/v4",
    },
    "bitbucket.org": {
        "provider": "bitbucket",
        "base_url": "https://api.bitbucket.org/2.0",
    },
}


def load_dotenv() -> None:
    path = os.environ.get("GIT_LANTERN_ENV", os.path.join(os.getcwd(), ".env"))
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def repo_depth(root: str, path: str) -> int:
    rel = os.path.relpath(path, root)
    if rel == ".":
        return 0
    return rel.count(os.sep) + 1


def _is_git_repo_root(path: str) -> bool:
    git_dir = os.path.join(path, ".git")
    if os.path.isdir(git_dir):
        return True
    if os.path.isfile(git_dir):
        try:
            with open(git_dir, "r", encoding="utf-8") as handle:
                first = handle.readline().strip()
        except OSError:
            return False
        if first.lower().startswith("gitdir:"):
            gitdir = first.split(":", 1)[1].strip()
            if ".git/modules/" in gitdir.replace("\\", "/"):
                return False
            return True
    return False


def find_repos(root: str, max_depth: int, include_hidden: bool) -> List[str]:
    matches = []
    for current, dirs, files in os.walk(root):
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        if _is_git_repo_root(current):
            matches.append(current)
            dirs[:] = []
            continue
        if max_depth is not None and repo_depth(root, current) >= max_depth:
            dirs[:] = []
    return sorted(matches, key=lambda path: (os.path.basename(path).lower(), path.lower()))


def _repo_name_for_sort(record: Dict[str, Any]) -> str:
    for field in ("repo", "name"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    path = str(record.get("path") or "").strip()
    if path:
        return os.path.basename(path)
    return ""


def _sort_records_by_repo_name(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            _repo_name_for_sort(record).lower(),
            str(record.get("path") or "").lower(),
        ),
    )


def build_repo_record(path: str, fetch: bool) -> Dict[str, str]:
    name = os.path.basename(path)
    if fetch:
        git.fetch(path)
    status = git.repo_status(path)
    return {
        "name": name,
        "path": path,
        "branch": status.get("branch"),
        "upstream": status.get("upstream"),
        "up_ahead": status.get("upstream_ahead"),
        "up_behind": status.get("upstream_behind"),
        "main_ref": status.get("main_ref"),
        "main_ahead": status.get("main_ahead"),
        "main_behind": status.get("main_behind"),
        "default_refs": status.get("default_refs"),
        "origin": git.get_origin_url(path),
    }


def _to_int_or_none(value) -> Optional[int]:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_divergence(ahead: Optional[int], behind: Optional[int]) -> str:
    if ahead is None and behind is None:
        return "-"
    if ahead == 0 and behind == 0:
        return "≡"
    ahead_str = "-" if ahead is None else str(ahead)
    behind_str = "-" if behind is None else str(behind)
    return f"{ahead_str}↑/{behind_str}↓"


def _progress_line(current: int, total: int, message: str) -> None:
    if total <= 0:
        return
    if sys.stderr.isatty():
        print(f"\r[{current}/{total}] {message:<60}", end="", file=sys.stderr, flush=True)
    else:
        print(f"[{current}/{total}] {message}", file=sys.stderr)


def _progress_done() -> None:
    if sys.stderr.isatty():
        print(file=sys.stderr)


def _collect_repo_records_with_progress(
    repos: List[str],
    fetch: bool,
    action_label: str,
) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    total = len(repos)
    for idx, path in enumerate(repos, start=1):
        repo_name = os.path.basename(path)
        verb = "fetch+status" if fetch else "status"
        _progress_line(idx, total, f"{action_label}: {verb} {repo_name}")
        records.append(build_repo_record(path, fetch))
    _progress_done()
    return records


def add_divergence_fields(record: MutableMapping[str, object]) -> MutableMapping[str, object]:
    up_ahead = _to_int_or_none(record.get("up_ahead"))
    up_behind = _to_int_or_none(record.get("up_behind"))
    main_ahead = _to_int_or_none(record.get("main_ahead"))
    main_behind = _to_int_or_none(record.get("main_behind"))

    up_value = _format_divergence(up_ahead, up_behind)
    main_value = _format_divergence(main_ahead, main_behind)

    record["up"] = up_value
    record["main"] = main_value
    return record


# --- TUI helpers ---


def _dialog_available() -> bool:
    """Check if dialog CLI is available."""
    return shutil.which("dialog") is not None


def _dialog_init() -> Tuple[int, int]:
    """Get dialog dimensions based on terminal size."""
    try:
        cols = int(subprocess.run(
            ["tput", "cols"], capture_output=True, text=True, check=True
        ).stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        cols = 120
    try:
        lines = int(subprocess.run(
            ["tput", "lines"], capture_output=True, text=True, check=True
        ).stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        lines = 40
    width = max(60, cols * 70 // 100)
    height = max(20, lines * 70 // 100)
    return height, width


def _dialog_menu(
    title: str,
    text: str,
    items: List[Tuple[str, str]],
    height: int = 0,
    width: int = 0,
) -> Optional[str]:
    """Show a dialog menu and return the selected item tag, or None if cancelled."""
    if height == 0 or width == 0:
        height, width = _dialog_init()
    menu_height = min(len(items), height - 8)
    cmd = [
        "dialog", "--stdout", "--title", title, "--menu", text,
        str(height), str(width), str(menu_height),
    ]
    for tag, description in items:
        cmd.extend([tag, description])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _dialog_checklist(
    title: str,
    text: str,
    items: List[Tuple[str, str, bool]],
    height: int = 0,
    width: int = 0,
) -> List[str]:
    """Show a dialog checklist and return selected tags."""
    if height == 0 or width == 0:
        height, width = _dialog_init()
    menu_height = min(len(items), height - 8)
    cmd = [
        "dialog", "--stdout", "--separate-output", "--title", title, "--checklist", text,
        str(height), str(width), str(max(menu_height, 1)),
    ]
    for tag, description, selected in items:
        cmd.extend([tag, description, "on" if selected else "off"])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _dialog_inputbox(
    title: str,
    text: str,
    default: str = "",
    height: int = 10,
    width: int = 60,
) -> Optional[str]:
    """Show a dialog input box and return the entered value, or None if cancelled."""
    cmd = [
        "dialog", "--stdout", "--title", title, "--inputbox", text,
        str(height), str(width), default,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _dialog_passwordbox(
    title: str,
    text: str,
    height: int = 10,
    width: int = 60,
) -> Optional[str]:
    """Show a dialog password box and return the entered value, or None if cancelled."""
    cmd = [
        "dialog", "--stdout", "--title", title, "--passwordbox", text,
        str(height), str(width),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _dialog_yesno(
    title: str,
    text: str,
    height: int = 10,
    width: int = 60,
) -> bool:
    """Show a dialog yes/no box and return True for yes, False for no."""
    cmd = [
        "dialog", "--title", title, "--yesno", text,
        str(height), str(width),
    ]
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


def _dialog_msgbox(title: str, text: str, height: int = 10, width: int = 60) -> None:
    """Show a dialog message box."""
    cmd = ["dialog", "--title", title, "--msgbox", text, str(height), str(width)]
    subprocess.run(cmd, check=False)


def _dialog_infobox(title: str, text: str, height: int = 10, width: int = 70) -> None:
    """Show a non-blocking informational dialog."""
    cmd = ["dialog", "--title", title, "--infobox", text, str(height), str(width)]
    subprocess.run(cmd, check=False)


def _lazygit_path() -> Optional[str]:
    return shutil.which("lazygit")


def _launch_lazygit(repo_path: str) -> int:
    binary = _lazygit_path()
    if not binary:
        print("lazygit is not installed or not in PATH.", file=sys.stderr)
        return 1
    subprocess.run(["clear"], check=False)
    result = subprocess.run([binary], cwd=repo_path, check=False)
    subprocess.run(["clear"], check=False)
    return result.returncode


def _run_git_op(repo_path: str, args: List[str], quiet: bool = True) -> int:
    kwargs: Dict[str, Any] = {"check": False}
    if quiet:
        kwargs.update(
            {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "text": True,
            }
        )
    result = subprocess.run(["git", "-C", repo_path, *args], **kwargs)
    return result.returncode


def _remote_main_ref(path: str) -> str:
    refs = git.get_default_branch_refs(path)
    if "origin" in refs:
        return refs["origin"]
    for candidate in ("origin/main", "origin/master"):
        if git.run_git(path, ["rev-parse", "--verify", candidate]):
            return candidate
    for ref in refs.values():
        return ref
    return ""


def _build_local_state_records(root: str, max_depth: int, include_hidden: bool, fetch: bool) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for path in find_repos(root, max_depth, include_hidden):
        record = add_divergence_fields(build_repo_record(path, fetch))
        record["clean"] = "yes" if git.is_clean(path) else "no"
        records.append(record)
    records.sort(key=lambda r: str(r.get("name", "")).lower())
    return records


def _resolve_selected_records(records: List[Dict[str, str]], repos_csv: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    names_or_paths = [x.strip() for x in (repos_csv or "").split(",") if x.strip()]
    if not names_or_paths:
        return records, None
    by_name: Dict[str, List[Dict[str, str]]] = {}
    by_path: Dict[str, Dict[str, str]] = {}
    for rec in records:
        name = str(rec.get("name") or "")
        by_name.setdefault(name, []).append(rec)
        p = str(rec.get("path") or "")
        by_path[p] = rec

    selected: List[Dict[str, str]] = []
    for key in names_or_paths:
        if key in by_path:
            selected.append(by_path[key])
            continue
        matches = by_name.get(key, [])
        if len(matches) == 1:
            selected.append(matches[0])
            continue
        if not matches:
            return [], f"Repository not found: {key}"
        return [], f"Repository name is ambiguous: {key}. Use full path."
    return selected, None


def _apply_bulk_action(
    records: List[Dict[str, str]],
    action: str,
    dry_run: bool,
    only_clean: bool,
) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for rec in records:
        name = str(rec.get("name") or "")
        path = str(rec.get("path") or "")
        upstream = str(rec.get("upstream") or "")
        clean = str(rec.get("clean") or "no")
        result = "skip"
        detail = ""

        if only_clean and clean != "yes":
            results.append({"repo": name, "action": action, "result": "skip-dirty", "path": path})
            continue

        if action == "update":
            if not upstream:
                results.append({"repo": name, "action": action, "result": "skip-no-upstream", "path": path})
                continue
            if dry_run:
                result = "dry-run"
                detail = "fetch+pull"
            else:
                rc_fetch = _run_git_op(path, ["fetch", "--prune"])
                rc_pull = _run_git_op(path, ["pull", "--ff-only"]) if rc_fetch == 0 else 1
                result = "ok" if (rc_fetch == 0 and rc_pull == 0) else "fail"
                detail = "fetch+pull"

        elif action == "checkout-main":
            main_ref = _remote_main_ref(path)
            if not main_ref:
                results.append({"repo": name, "action": action, "result": "skip-no-main-ref", "path": path})
                continue
            branch = main_ref.split("/", 1)[1] if "/" in main_ref else main_ref
            if dry_run:
                result = "dry-run"
                detail = f"{branch} <= {main_ref}"
            else:
                rc_fetch = _run_git_op(path, ["fetch", "--prune"])
                if rc_fetch != 0:
                    result = "fail"
                else:
                    rc_checkout = _run_git_op(path, ["checkout", branch])
                    if rc_checkout != 0:
                        rc_checkout = _run_git_op(path, ["checkout", "-b", branch, "--track", main_ref])
                    rc_pull = _run_git_op(path, ["pull", "--ff-only"]) if rc_checkout == 0 else 1
                    result = "ok" if (rc_checkout == 0 and rc_pull == 0) else "fail"
                detail = f"{branch} <= {main_ref}"

        elif action == "push":
            if not upstream:
                results.append({"repo": name, "action": action, "result": "skip-no-upstream", "path": path})
                continue
            if dry_run:
                result = "dry-run"
                detail = "push"
            else:
                rc = _run_git_op(path, ["push"])
                result = "ok" if rc == 0 else "fail"
                detail = "push"

        results.append(
            {
                "repo": name,
                "action": action,
                "result": result if not detail else f"{result}:{detail}",
                "path": path,
            }
        )
    return results


def cmd_config_setup(args: argparse.Namespace) -> int:
    """Interactive TUI for setting up server configuration."""
    if not _dialog_available():
        print("dialog is required for --tui / config setup.", file=sys.stderr)
        print("Install it with: apt install dialog (Debian/Ubuntu) or brew install dialog (macOS)", file=sys.stderr)
        return 1

    height, width = _dialog_init()
    config = lantern_config.load_config()
    servers = config.get("servers", {}) if isinstance(config.get("servers"), dict) else {}

    while True:
        # Build menu items
        menu_items: List[Tuple[str, str]] = [
            ("add", "Add a new server"),
        ]
        if servers:
            menu_items.append(("edit", "Edit existing server"))
            menu_items.append(("remove", "Remove a server"))
            menu_items.append(("default", "Set default server"))
        menu_items.append(("save", "Save and exit"))
        menu_items.append(("cancel", "Exit without saving"))

        action = _dialog_menu(
            "Server Configuration",
            "Manage your Git servers.\n\nCurrently configured: " + (", ".join(servers.keys()) if servers else "none"),
            menu_items,
            height,
            width,
        )

        if action is None or action == "cancel":
            return 0

        if action == "save":
            config["servers"] = servers
            output_path = lantern_config.config_path()
            try:
                _write_json_secure(output_path, config)
            except OSError as exc:
                _dialog_msgbox("Error", f"Failed to save config: {exc}")
                return 1
            _dialog_msgbox("Success", f"Configuration saved to:\n{output_path}")
            return 0

        if action == "add":
            # Select preset or custom
            preset_items: List[Tuple[str, str]] = []
            for name, preset in SERVER_PRESETS.items():
                status = "(configured)" if name in servers else ""
                preset_items.append((name, f"{preset['provider']} {status}"))
            preset_items.append(("custom", "Add custom server"))

            server_choice = _dialog_menu(
                "Add Server",
                "Select a server to add:",
                preset_items,
                height,
                width,
            )
            if not server_choice:
                continue

            if server_choice == "custom":
                server_name = _dialog_inputbox("Server Name", "Enter the server hostname (e.g., gitlab.example.com):")
                if not server_name:
                    continue
                provider = _dialog_menu(
                    "Provider",
                    f"Select the provider type for {server_name}:",
                    [("github", "GitHub or GitHub Enterprise"),
                     ("gitlab", "GitLab or GitLab self-hosted"),
                     ("bitbucket", "Bitbucket or Bitbucket Server")],
                )
                if not provider:
                    continue
                base_url = _dialog_inputbox(
                    "Base URL",
                    f"Enter the API base URL for {server_name}:",
                    forge.DEFAULT_BASE_URLS.get(provider, ""),
                )
                if base_url is None:
                    continue
                server_config = {"provider": provider}
                if base_url:
                    server_config["base_url"] = base_url
            else:
                server_name = server_choice
                server_config = dict(SERVER_PRESETS[server_name])

            # Get username
            existing_user = servers.get(server_name, {}).get("user", "")
            user = _dialog_inputbox(
                "Username",
                f"Enter your username for {server_name}:",
                existing_user,
            )
            if user is None:
                continue
            if user:
                server_config["user"] = user

            # Optionally get token
            if _dialog_yesno("Token", f"Do you want to add an API token for {server_name}?"):
                token = _dialog_passwordbox("Token", f"Enter your API token for {server_name}:")
                if token:
                    server_config["token"] = token

            servers[server_name] = server_config
            _dialog_msgbox("Added", f"Server '{server_name}' has been added.\n\nRemember to save before exiting.")

        elif action == "edit":
            server_list = [(name, servers[name].get("provider", "unknown")) for name in servers]
            server_to_edit = _dialog_menu("Edit Server", "Select a server to edit:", server_list, height, width)
            if not server_to_edit:
                continue

            server_config = dict(servers[server_to_edit])

            # Edit username
            current_user = server_config.get("user", "")
            new_user = _dialog_inputbox(
                "Username",
                f"Enter username for {server_to_edit}:",
                current_user,
            )
            if new_user is not None:
                if new_user:
                    server_config["user"] = new_user
                elif "user" in server_config:
                    del server_config["user"]

            # Edit token
            has_token = bool(server_config.get("token"))
            token_prompt = "Update token?" if has_token else "Add token?"
            if _dialog_yesno("Token", f"{token_prompt} for {server_to_edit}?"):
                new_token = _dialog_passwordbox("Token", f"Enter API token for {server_to_edit}:")
                if new_token:
                    server_config["token"] = new_token
            elif has_token and _dialog_yesno("Remove Token", f"Remove existing token for {server_to_edit}?"):
                del server_config["token"]

            servers[server_to_edit] = server_config
            _dialog_msgbox("Updated", f"Server '{server_to_edit}' has been updated.\n\nRemember to save before exiting.")

        elif action == "remove":
            server_list = [(name, servers[name].get("provider", "unknown")) for name in servers]
            server_to_remove = _dialog_menu("Remove Server", "Select a server to remove:", server_list, height, width)
            if not server_to_remove:
                continue
            if _dialog_yesno("Confirm", f"Are you sure you want to remove '{server_to_remove}'?"):
                del servers[server_to_remove]
                _dialog_msgbox("Removed", f"Server '{server_to_remove}' has been removed.\n\nRemember to save before exiting.")

        elif action == "default":
            server_list = [(name, servers[name].get("provider", "unknown")) for name in servers]
            current_default = config.get("default_server", "")
            default_server = _dialog_menu(
                "Default Server",
                f"Select the default server (current: {current_default or 'none'}):",
                server_list,
                height,
                width,
            )
            if default_server:
                config["default_server"] = default_server
                _dialog_msgbox("Updated", f"Default server set to '{default_server}'.\n\nRemember to save before exiting.")


def _run_lantern_subprocess(
    cmd_args: List[str],
    height: int,
    width: int,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a lantern subprocess with correct PYTHONPATH and error handling."""
    kwargs: dict = {
        "check": False,
        "text": True,
        "env": {**os.environ, "PYTHONPATH": _SRC_DIR},
    }
    if capture:
        kwargs["capture_output"] = True
    result = subprocess.run(cmd_args, **kwargs)
    if result.returncode != 0 and capture:
        stderr = (result.stderr or "").strip()
        if stderr:
            _dialog_msgbox("Error", f"Command failed:\n{stderr}", height, width)
    return result


def _validate_session_root(root: str, height: int, width: int) -> bool:
    """Check that root is a valid directory, showing an error dialog if not."""
    if os.path.isdir(root):
        return True
    _dialog_msgbox("Error", f"Root directory not found: {root}\n\nUse 'settings' to change it.", height, width)
    return False


def _dialog_textbox_from_text(title: str, text: str, height: int = 0, width: int = 0) -> None:
    """Display text in a scrollable dialog textbox using a temp file."""
    if height == 0 or width == 0:
        height, width = _dialog_init()
    fd, tmp_path = tempfile.mkstemp(prefix=".lantern-tui.", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        cmd = ["dialog", "--title", title, "--textbox", tmp_path, str(height), str(width)]
        subprocess.run(cmd, check=False)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _fleet_action_parts_for_row(
    row: Dict[str, str],
    clone_missing: bool,
    pull_behind: bool,
    push_ahead: bool,
    checkout_branch: str,
    checkout_pr: str,
) -> List[str]:
    state = str(row.get("state") or "")
    parts: List[str] = []
    if state == "missing-local" and clone_missing:
        parts.append("clone")
    if state == "behind-remote" and pull_behind:
        parts.append("pull")
    if state == "ahead-remote" and push_ahead:
        parts.append("push")
    if checkout_pr:
        parts.append(f"checkout-pr:{checkout_pr}")
    elif checkout_branch:
        parts.append(f"checkout:{checkout_branch}")
    if not parts:
        parts.append("skip")
    return parts


def _fleet_preflight_confirm(
    title: str,
    rows: List[Dict[str, str]],
    clone_missing: bool,
    pull_behind: bool,
    push_ahead: bool,
    checkout_branch: str,
    checkout_pr: str,
    dry_run: bool,
    only_clean: bool,
    height: int,
    width: int,
) -> List[Dict[str, str]]:
    prepared: List[Dict[str, Any]] = []
    for row in rows:
        plan = ", ".join(
            _fleet_action_parts_for_row(
                row=row,
                clone_missing=clone_missing,
                pull_behind=pull_behind,
                push_ahead=push_ahead,
                checkout_branch=checkout_branch,
                checkout_pr=checkout_pr,
            )
        )
        prepared.append(
            {
                "row": row,
                "repo": str(row.get("repo") or ""),
                "state": str(row.get("state") or "-"),
                "plan": plan,
                "clean": str(row.get("clean") or "-"),
                "path": str(row.get("path") or ""),
            }
        )

    prepared.sort(key=lambda entry: (entry["repo"].lower(), entry["path"].lower()))
    summary_rows = [
        {
            "repo": entry["repo"],
            "state": entry["state"],
            "plan": entry["plan"],
            "clean": entry["clean"],
            "path": entry["path"],
        }
        for entry in prepared
    ]
    summary_header = [
        f"Repos selected: {len(prepared)}",
        f"Clone missing: {'yes' if clone_missing else 'no'}",
        f"Pull behind: {'yes' if pull_behind else 'no'}",
        f"Push ahead: {'yes' if push_ahead else 'no'}",
    ]
    if checkout_branch:
        summary_header.append(f"Checkout branch: {checkout_branch}")
    if checkout_pr:
        summary_header.append(f"Checkout PR: {checkout_pr}")
    summary_header.append(f"Dry run: {'yes' if dry_run else 'no'}")
    summary_header.append(f"Only clean: {'yes' if only_clean else 'no'}")
    summary_header.append("")
    summary_header.append("Planned actions by repository:")
    summary_text = "\n".join(summary_header) + "\n\n" + render_table(
        summary_rows,
        ["repo", "state", "plan", "clean", "path"],
    )
    _dialog_textbox_from_text(title, summary_text, height, width)

    checklist_items: List[Tuple[str, str, bool]] = []
    idx_to_row: Dict[str, Dict[str, str]] = {}
    for idx, entry in enumerate(prepared, start=1):
        tag = str(idx)
        idx_to_row[tag] = entry["row"]
        desc = f"{entry['repo']} [{entry['state']}] -> {entry['plan']}"
        checklist_items.append((tag, desc, True))
    selected_tags = _dialog_checklist(
        title,
        "Confirm repositories to process (uncheck to skip):",
        checklist_items,
        height,
        width,
    )
    if not selected_tags:
        return []
    selected_set = set(selected_tags)
    return [idx_to_row[tag] for tag in sorted(idx_to_row.keys(), key=lambda x: int(x)) if tag in selected_set]


def _fleet_log_path(root: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = os.path.join(root, "data", "fleet-logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"fleet-apply-{ts}.json")


def _fleet_short_summary_from_log(log_path: str) -> str:
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return f"Fleet log saved:\n{log_path}"

    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    results = payload.get("results", []) if isinstance(payload.get("results"), list) else []

    updated_repos: List[str] = []
    branch_updates: List[str] = []
    for rec in results:
        if not isinstance(rec, dict):
            continue
        repo = str(rec.get("repo") or "")
        actions = rec.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        changed = False
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action") or "")
            status = str(action.get("status") or "")
            if status in {"ok", "dry-run"} and action_name in {"clone", "pull", "push"}:
                changed = True
            if action_name == "checkout" and status in {"ok", "dry-run"}:
                branch = str(action.get("branch") or "")
                if repo and branch:
                    branch_updates.append(f"{repo}:{branch}")
                    changed = True
        if changed and repo:
            updated_repos.append(repo)

    lines = [
        "Fleet apply summary:",
        f"Total repos processed: {int(summary.get('repos_processed', 0) or 0)}",
        f"Repos updated: {len(updated_repos)}",
        f"Branch updates: {len(branch_updates)}",
        "",
    ]
    if updated_repos:
        lines.append("Updated repos:")
        lines.extend(f"- {name}" for name in updated_repos[:15])
        if len(updated_repos) > 15:
            lines.append(f"- ... and {len(updated_repos) - 15} more")
        lines.append("")
    if branch_updates:
        lines.append("Branch changes:")
        lines.extend(f"- {item}" for item in branch_updates[:15])
        if len(branch_updates) > 15:
            lines.append(f"- ... and {len(branch_updates) - 15} more")
        lines.append("")
    lines.append(f"Full log: {log_path}")
    return "\n".join(lines)


def _fleet_logs_dir(root: str) -> str:
    return os.path.join(root, "data", "fleet-logs")


def _fleet_log_files(root: str) -> List[str]:
    log_dir = _fleet_logs_dir(root)
    if not os.path.isdir(log_dir):
        return []
    files: List[str] = []
    for name in os.listdir(log_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(log_dir, name)
        if os.path.isfile(path):
            files.append(path)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def cmd_fleet_logs(args: argparse.Namespace) -> int:
    log_path = str(args.input or "").strip()
    if not log_path:
        logs = _fleet_log_files(args.root)
        if not logs:
            print(f"No fleet logs found in: {_fleet_logs_dir(args.root)}")
            return 0
        if args.latest:
            log_path = logs[0]
        else:
            records = []
            for path in logs[: max(1, int(args.limit))]:
                records.append({"timestamp": os.path.basename(path), "path": path})
            print(render_table(records, ["timestamp", "path"]))
            return 0

    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to read fleet log '{log_path}': {exc}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print(f"Invalid fleet log format: {log_path}", file=sys.stderr)
        return 1

    if not bool(getattr(args, "no_pretty", False)):
        jq_bin = shutil.which("jq")
        if jq_bin:
            proc = subprocess.run([jq_bin, ".", log_path], check=False)
            if proc.returncode == 0:
                return 0
            print(f"jq failed for '{log_path}', falling back to built-in output.", file=sys.stderr)
        else:
            print("jq is not installed; falling back to built-in output.", file=sys.stderr)

    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    options = payload.get("options", {}) if isinstance(payload.get("options"), dict) else {}
    branch_updates = payload.get("branch_updates", []) if isinstance(payload.get("branch_updates"), list) else []
    results = payload.get("results", []) if isinstance(payload.get("results"), list) else []

    print(f"log={log_path}")
    print(f"generated_at={payload.get('generated_at', '-')}")
    print(
        "repos_targeted={targeted} repos_processed={processed} repos_updated={updated} branch_updates={branches}".format(
            targeted=int(summary.get("repos_targeted", 0) or 0),
            processed=int(summary.get("repos_processed", 0) or 0),
            updated=int(summary.get("repos_updated", 0) or 0),
            branches=int(summary.get("branch_updates", 0) or 0),
        )
    )
    print(
        "clone_missing={clone} pull_behind={pull} push_ahead={push} dry_run={dry} only_clean={clean}".format(
            clone="yes" if options.get("clone_missing") else "no",
            pull="yes" if options.get("pull_behind") else "no",
            push="yes" if options.get("push_ahead") else "no",
            dry="yes" if options.get("dry_run") else "no",
            clean="yes" if options.get("only_clean") else "no",
        )
    )

    if branch_updates:
        rows = []
        for item in branch_updates[: max(1, int(args.limit))]:
            if not isinstance(item, dict):
                continue
            rows.append({"repo": str(item.get("repo") or ""), "branch": str(item.get("branch") or "")})
        if rows:
            print("\nBranch updates:")
            print(render_table(rows, ["repo", "branch"]))

    if args.show_results:
        result_rows: List[Dict[str, str]] = []
        for rec in results[: max(1, int(args.limit))]:
            if not isinstance(rec, dict):
                continue
            result_rows.append(
                {
                    "repo": str(rec.get("repo") or ""),
                    "state": str(rec.get("state") or ""),
                    "result": str(rec.get("result") or ""),
                    "path": str(rec.get("path") or ""),
                }
            )
        if result_rows:
            print("\nResults:")
            print(render_table(result_rows, ["repo", "state", "result", "path"]))

    return 0


def _default_repo_list_candidates(root: str) -> List[str]:
    root_abs = os.path.abspath(root)
    cwd = os.getcwd()
    rels = [
        os.path.join("data", "github.json"),
        os.path.join("data", "gitlab.json"),
        os.path.join("data", "bitbucket.json"),
    ]
    candidates: List[str] = []
    for base in (root_abs, cwd):
        for rel in rels:
            candidates.append(os.path.abspath(os.path.join(base, rel)))
    seen = set()
    unique: List[str] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _resolve_existing_repo_list_file(root: str) -> str:
    for path in _default_repo_list_candidates(root):
        if os.path.isfile(path):
            return path
    return ""


def _persist_workspace_root(root_path: str) -> Optional[str]:
    try:
        config = lantern_config.load_config()
        config["workspace_root"] = os.path.abspath(root_path)
        _write_json_secure(lantern_config.config_path(), config)
    except OSError as exc:
        return str(exc)
    return None


def _persist_scan_json_path(scan_path: str) -> Optional[str]:
    try:
        config = lantern_config.load_config()
        config["scan_json_path"] = os.path.abspath(scan_path)
        _write_json_secure(lantern_config.config_path(), config)
    except OSError as exc:
        return str(exc)
    return None


def cmd_tui(args: argparse.Namespace) -> int:
    """Main TUI interface for lantern."""
    if not _dialog_available():
        print("dialog is required for --tui mode.", file=sys.stderr)
        print("Install it with: apt install dialog (Debian/Ubuntu) or brew install dialog (macOS)", file=sys.stderr)
        return 1

    height, width = _dialog_init()

    config = lantern_config.load_config()
    configured_root = str(config.get("workspace_root") or "").strip()
    cli_root = str(getattr(args, "root", "") or "").strip()
    initial_root = cli_root or configured_root
    if initial_root and not os.path.isdir(initial_root):
        initial_root = ""
    if not initial_root:
        root_prompt_default = os.getcwd()
        root_value = _dialog_inputbox(
            "Workspace Root",
            "Enter workspace root directory for repository operations:",
            root_prompt_default,
        )
        if not root_value:
            return 0
        if not os.path.isdir(root_value):
            _dialog_msgbox("Error", f"Directory not found: {root_value}", height, width)
            return 1
        initial_root = os.path.abspath(root_value)
        err = _persist_workspace_root(initial_root)
        if err:
            _dialog_msgbox("Warning", f"Could not persist workspace root:\n{err}", height, width)
    else:
        initial_root = os.path.abspath(initial_root)

    configured_scan_path = str(config.get("scan_json_path") or "").strip()
    default_scan_path = os.path.abspath(os.path.join(initial_root, "data", "repos.json"))
    if configured_scan_path:
        scan_path = os.path.abspath(configured_scan_path)
    else:
        if os.path.isfile(default_scan_path):
            scan_path = default_scan_path
            _persist_scan_json_path(scan_path)
        else:
            scan_candidate = _dialog_inputbox(
                "Scan JSON Path",
                "Enter JSON scan file path (used by table/report and scan output):",
                default_scan_path,
            )
            if not scan_candidate:
                return 0
            scan_path = os.path.abspath(scan_candidate)
            err = _persist_scan_json_path(scan_path)
            if err:
                _dialog_msgbox("Warning", f"Could not persist scan path:\n{err}", height, width)

    # Session-level settings (persist throughout the TUI session)
    session = {
        "root": initial_root,
        "scan_path": scan_path,
        "max_depth": 6,
        "include_hidden": False,
        "include_forks": False,
    }

    while True:
        subprocess.run(["clear"], check=False)

        # Build menu with current settings shown
        hidden_flag = "yes" if session["include_hidden"] else "no"
        forks_flag = "yes" if session["include_forks"] else "no"
        menu_text = (
            f"Select an operation:\n\n"
            f"Root: {session['root']}  |  Depth: {session['max_depth']}  |  "
            f"Hidden: {hidden_flag}  |  Forks: {forks_flag}\n"
            f"Scan JSON: {session['scan_path']}"
        )
        menu_items: List[Tuple[str, str]] = [
            ("servers", "List configured Git servers"),
            ("config", "Server configuration"),
            ("settings", "Session settings"),
            ("repos", "List local repositories"),
            ("status", "Show repository status"),
            ("lazygit", "Open repository in lazygit"),
            ("fleet", "Unified fleet plan/apply (clone, pull, push)"),
            ("scan", "Scan repositories to JSON"),
            ("table", "Render table from JSON scan"),
            ("find", "Find repositories by name/remote"),
            ("duplicates", "Find duplicate repositories"),
            ("forge", "Git forge operations (list/clone)"),
            ("report", "Export report (CSV/JSON/MD)"),
            ("command", "Run any lantern CLI command"),
            ("exit", "Exit"),
        ]

        action = _dialog_menu(
            "Git Lantern",
            menu_text,
            menu_items,
            height,
            width,
        )

        if action is None or action == "exit":
            # Clear screen on exit
            subprocess.run(["clear"], check=False)
            return 0

        if action == "settings":
            hidden_label = "ON" if session["include_hidden"] else "OFF"
            forks_label = "ON" if session["include_forks"] else "OFF"
            settings_items: List[Tuple[str, str]] = [
                ("depth", f"Max scan depth (current: {session['max_depth']})"),
                ("hidden", f"Include hidden directories ({hidden_label})"),
                ("forks", f"Include forks in forge list ({forks_label})"),
                ("back", "Back to main menu"),
            ]
            settings_action = _dialog_menu("Session Settings", "Configure session settings:", settings_items, height, width)

            if settings_action == "depth":
                depth_str = _dialog_inputbox("Max Depth", "Enter max scan depth (1-20):", str(session["max_depth"]))
                if depth_str:
                    try:
                        depth_val = int(depth_str)
                        if 1 <= depth_val <= 20:
                            session["max_depth"] = depth_val
                            _dialog_msgbox("Settings", f"Max depth set to: {depth_val}")
                        else:
                            _dialog_msgbox("Error", "Depth must be between 1 and 20.")
                    except ValueError:
                        _dialog_msgbox("Error", "Invalid number.")
            elif settings_action == "hidden":
                session["include_hidden"] = not session["include_hidden"]
                state = "ON" if session["include_hidden"] else "OFF"
                _dialog_msgbox("Settings", f"Include hidden directories: {state}")
            elif settings_action == "forks":
                session["include_forks"] = not session["include_forks"]
                state = "ON" if session["include_forks"] else "OFF"
                _dialog_msgbox("Settings", f"Include forks in forge list: {state}")

        elif action == "servers":
            # Show servers in a message box
            config = lantern_config.load_config()
            records = lantern_config.list_servers(config)
            if not records:
                _dialog_msgbox("Servers", "No servers configured.\n\nUse 'config' > 'setup' to add servers.")
            else:
                lines = ["Configured servers:", ""]
                for rec in records:
                    lines.append(f"  {rec['name']} ({rec['provider']})")
                    if rec.get('user'):
                        lines.append(f"    User: {rec['user']}")
                    if rec.get('base_url'):
                        lines.append(f"    URL: {rec['base_url']}")
                    lines.append("")
                _dialog_msgbox("Servers", "\n".join(lines))

        elif action == "config":
            config_items: List[Tuple[str, str]] = [
                ("workspace", f"Set workspace root (current: {session['root']})"),
                ("scan_path", f"Set scan JSON path (current: {session['scan_path']})"),
                ("setup", "Interactive server setup"),
                ("path", "Show config file path"),
                ("export", "Export config to JSON"),
                ("import", "Import config from JSON"),
                ("back", "Back to main menu"),
            ]
            config_action = _dialog_menu("Configuration", "Select an operation:", config_items, height, width)

            if config_action == "workspace":
                new_root = _dialog_inputbox(
                    "Workspace Root",
                    "Enter workspace root directory:",
                    session["root"],
                )
                if new_root:
                    if not os.path.isdir(new_root):
                        _dialog_msgbox("Error", f"Directory not found: {new_root}", height, width)
                    else:
                        session["root"] = os.path.abspath(new_root)
                        err = _persist_workspace_root(session["root"])
                        if err:
                            _dialog_msgbox("Warning", f"Could not persist workspace root:\n{err}", height, width)
                        else:
                            _dialog_msgbox("Configuration", f"Workspace root saved:\n{session['root']}", height, width)
            elif config_action == "scan_path":
                new_scan = _dialog_inputbox(
                    "Scan JSON Path",
                    "Enter scan JSON path:",
                    session["scan_path"],
                )
                if new_scan:
                    session["scan_path"] = os.path.abspath(new_scan)
                    err = _persist_scan_json_path(session["scan_path"])
                    if err:
                        _dialog_msgbox("Warning", f"Could not persist scan path:\n{err}", height, width)
                    else:
                        _dialog_msgbox("Configuration", f"Scan path saved:\n{session['scan_path']}", height, width)
            elif config_action == "setup":
                # Create a namespace for the config setup command
                setup_args = argparse.Namespace()
                cmd_config_setup(setup_args)
            elif config_action == "path":
                path = lantern_config.config_path()
                _dialog_msgbox("Config Path", f"Configuration file path:\n\n{path}")
            elif config_action == "export":
                output = _dialog_inputbox("Export", "Enter output file path:", "git-lantern-servers.json")
                if output:
                    export_args = argparse.Namespace(output=output, include_secrets=False)
                    result = cmd_config_export(export_args)
                    if result == 0:
                        _dialog_msgbox("Export", f"Configuration exported to:\n{output}")
                    else:
                        _dialog_msgbox("Error", "Failed to export configuration.")
            elif config_action == "import":
                input_file = _dialog_inputbox("Import", "Enter input file path:", "git-lantern-servers.json")
                if input_file and os.path.isfile(input_file):
                    replace = _dialog_yesno("Replace", "Replace existing servers? (No = merge)")
                    import_args = argparse.Namespace(input=input_file, output="", replace=replace)
                    result = cmd_config_import(import_args)
                    if result == 0:
                        _dialog_msgbox("Import", "Configuration imported successfully.")
                    else:
                        _dialog_msgbox("Error", "Failed to import configuration.")
                elif input_file:
                    _dialog_msgbox("Error", f"File not found: {input_file}")

        elif action == "repos":
            if not _validate_session_root(session["root"], height, width):
                continue
            # Run repos command and display output
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            if not repos_list:
                _dialog_msgbox("Repositories", f"No repositories found in:\n{session['root']}")
                continue
            records = []
            for path in repos_list:
                records.append({
                    "name": os.path.basename(path),
                    "path": path,
                    "origin": git.get_origin_url(path),
                })
            records = _sort_records_by_repo_name(records)
            output_text = render_table(records, ["name", "path", "origin"])
            _dialog_textbox_from_text("Repositories", output_text, height, width)

        elif action == "status":
            if not _validate_session_root(session["root"], height, width):
                continue
            fetch = _dialog_yesno("Fetch", "Run 'git fetch' before showing status?")
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            if not repos_list:
                _dialog_msgbox("Status", f"No repositories found in:\n{session['root']}")
                continue
            _dialog_infobox(
                "Status",
                "Collecting repository status...\n\nPlease wait.",
                max(8, height // 3),
                max(60, width // 2),
            )
            records = [add_divergence_fields(record) for record in _collect_repo_records_with_progress(repos_list, fetch, "status")]
            subprocess.run(["clear"], check=False)
            records = _sort_records_by_repo_name(records)
            columns = ["name", "branch", "upstream", "up", "main_ref", "main"]
            output_text = render_table(records, columns)
            _dialog_textbox_from_text("Status", output_text, height, width)

        elif action == "fleet":
            if not _validate_session_root(session["root"], height, width):
                continue
            config = lantern_config.load_config()
            server_records = lantern_config.list_servers(config)
            default_server = lantern_config.get_server_name(config, "")
            server = default_server
            if server_records:
                server_items = [("default", f"Use default ({default_server})")]
                server_items.extend([(rec["name"], rec["provider"]) for rec in server_records])
                server_choice = _dialog_menu("Fleet Server", "Choose server for remote comparison:", server_items, height, width)
                if not server_choice:
                    continue
                if server_choice != "default":
                    server = server_choice
            selected_server = lantern_config.get_server(config, server)
            selected_provider = str(selected_server.get("provider") or "github").lower()
            fetch = _dialog_yesno("Fetch", "Run local git fetch before building fleet plan?")
            include_prs = _dialog_yesno("PR Info", "Include fresh open PR numbers/branches in plan?")
            fleet_items: List[Tuple[str, str]] = [
                ("smart_sync", "Smart Sync (preset multi-repo update)"),
                ("plan", "Show fleet reconciliation plan"),
                ("apply_all", "Apply clone/pull/push for all eligible repos"),
                ("apply_select", "Apply actions only for selected repos"),
                ("back", "Back to main menu"),
            ]
            fleet_action = _dialog_menu("Fleet", "Select an operation:", fleet_items, height, width)
            if not fleet_action or fleet_action == "back":
                continue

            common_opts = [
                "--root", session["root"],
                "--max-depth", str(session["max_depth"]),
                "--server", server,
            ]
            if session["include_hidden"]:
                common_opts.append("--include-hidden")
            if session["include_forks"]:
                common_opts.append("--include-forks")

            if fleet_action == "smart_sync":
                preset_items: List[Tuple[str, str]] = [
                    ("fast_pull", "Fast Pull (pull behind repos)"),
                    ("branch_rollout", "Branch Rollout (checkout/update branch)"),
                    ("pr_rollout", "PR Rollout (checkout PR branch)"),
                    ("full_reconcile", "Full Reconcile (clone/pull/push)"),
                ]
                preset = _dialog_menu("Smart Sync", "Choose a preset:", preset_items, height, width)
                if not preset:
                    continue
                if preset == "pr_rollout" and selected_provider != "github":
                    _dialog_msgbox("Smart Sync", "PR Rollout is currently supported only for GitHub servers.", height, width)
                    continue

                smart_fetch = True
                with_prs = preset in {"branch_rollout", "pr_rollout"}
                _dialog_infobox(
                    "Smart Sync",
                    "Preparing fleet context...\n\nPlease wait.",
                    max(8, height // 3),
                    max(60, width // 2),
                )
                smart_plan_args = argparse.Namespace(
                    root=session["root"],
                    max_depth=session["max_depth"],
                    include_hidden=session["include_hidden"],
                    fetch=smart_fetch,
                    server=server,
                    input="",
                    user="",
                    token="",
                    include_forks=session["include_forks"],
                    with_prs=with_prs,
                    pr_stale_days=30,
                )
                try:
                    smart_rows, _smart_meta = _fleet_plan_records(smart_plan_args)
                except Exception as exc:
                    subprocess.run(["clear"], check=False)
                    _dialog_msgbox("Error", str(exc), height, width)
                    continue
                subprocess.run(["clear"], check=False)

                if preset == "fast_pull":
                    candidates = [r for r in smart_rows if str(r.get("state") or "") == "behind-remote"]
                elif preset == "full_reconcile":
                    candidates = [r for r in smart_rows if str(r.get("action") or "") in {"clone", "pull", "push"}]
                else:
                    candidates = [
                        r
                        for r in smart_rows
                        if str(r.get("state") or "") in {"in-sync", "behind-remote", "ahead-remote", "diverged", "missing-local"}
                    ]

                if not candidates:
                    _dialog_msgbox("Smart Sync", "No repositories match the selected preset.", height, width)
                    continue

                scope_items: List[Tuple[str, str]] = [
                    ("all", "All actionable repositories"),
                    ("clean", "Only clean repositories"),
                    ("select", "Pick repositories"),
                ]
                scope = _dialog_menu("Scope", "Choose repository scope:", scope_items, height, width)
                if not scope:
                    continue

                selected_rows = list(candidates)
                if scope == "clean":
                    selected_rows = [r for r in selected_rows if str(r.get("clean") or "") in {"yes", "-"}]
                elif scope == "select":
                    checklist_items: List[Tuple[str, str, bool]] = []
                    idx_to_repo: Dict[str, str] = {}
                    for i, row in enumerate(candidates, start=1):
                        tag = str(i)
                        repo_name = str(row.get("repo") or "")
                        idx_to_repo[tag] = repo_name
                        state = str(row.get("state") or "-")
                        action_name = str(row.get("action") or "-")
                        desc = f"{repo_name} [{state}] -> {action_name}"
                        checklist_items.append((tag, desc, True))
                    selected_tags = _dialog_checklist("Smart Sync", "Select repositories to process:", checklist_items, height, width)
                    if not selected_tags:
                        continue
                    selected_repos = {idx_to_repo[tag] for tag in selected_tags if tag in idx_to_repo}
                    selected_rows = [r for r in candidates if str(r.get("repo") or "") in selected_repos]

                if not selected_rows:
                    _dialog_msgbox("Smart Sync", "No repositories selected.", height, width)
                    continue

                checkout_branch = ""
                checkout_pr = ""
                if preset == "branch_rollout":
                    branch_hints: List[str] = []
                    for row in selected_rows:
                        b = str(row.get("latest_branch") or "").strip()
                        if b and b != "-" and b not in branch_hints:
                            branch_hints.append(b)
                    hint = ", ".join(branch_hints[:20]) if branch_hints else "No latest branch hints detected."
                    checkout_branch = (
                        _dialog_inputbox(
                            "Branch Rollout",
                            f"Enter branch to checkout/update.\n\nHints: {hint}",
                            branch_hints[0] if branch_hints else "",
                        )
                        or ""
                    ).strip()
                    if not checkout_branch:
                        continue
                elif preset == "pr_rollout":
                    pr_numbers: List[str] = []
                    for row in selected_rows:
                        for part in str(row.get("prs") or "").split(","):
                            p = part.strip()
                            if p and p != "-" and p not in pr_numbers:
                                pr_numbers.append(p)
                    hint = ", ".join(pr_numbers[:20]) if pr_numbers else "No fresh PR numbers detected."
                    checkout_pr = (
                        _dialog_inputbox(
                            "PR Rollout",
                            f"Enter PR number to checkout.\n\nDetected: {hint}",
                            pr_numbers[0] if pr_numbers else "",
                        )
                        or ""
                    ).strip()
                    if not checkout_pr:
                        continue

                dry_run = False
                only_clean = False
                if _dialog_yesno("Advanced", "Adjust advanced options (dry-run / only-clean)?", height, width):
                    dry_run = _dialog_yesno("Dry Run", "Perform a dry run (no changes)?")
                    only_clean = _dialog_yesno("Only Clean", "Skip dirty repos for pull/push?")

                include_push = False
                if preset == "full_reconcile":
                    push_choice = _dialog_menu(
                        "Push Mode",
                        "Should ahead repositories be pushed to remote?",
                        [
                            ("no_push", "No push (skip ahead repos)"),
                            ("push", "Push ahead repos to remote"),
                        ],
                        height,
                        width,
                    )
                    if not push_choice:
                        continue
                    include_push = push_choice == "push"

                apply_cmd = [sys.executable, "-m", "lantern", "fleet", "apply", *common_opts]
                apply_cmd.append("--fetch")
                clone_missing = False
                pull_behind = False
                if preset == "fast_pull":
                    pull_behind = True
                    apply_cmd.append("--pull-behind")
                elif preset == "full_reconcile":
                    clone_missing = True
                    pull_behind = True
                    apply_cmd.extend(["--clone-missing", "--pull-behind"])
                    if include_push:
                        apply_cmd.append("--push-ahead")
                else:
                    clone_missing = True
                    pull_behind = True
                    apply_cmd.extend(["--clone-missing", "--pull-behind"])
                if checkout_branch:
                    apply_cmd.extend(["--checkout-branch", checkout_branch])
                if checkout_pr:
                    apply_cmd.extend(["--checkout-pr", checkout_pr])
                if dry_run:
                    apply_cmd.append("--dry-run")
                if only_clean:
                    apply_cmd.append("--only-clean")

                confirmed_rows = _fleet_preflight_confirm(
                    title="Smart Sync Plan",
                    rows=selected_rows,
                    clone_missing=clone_missing,
                    pull_behind=pull_behind,
                    push_ahead=include_push,
                    checkout_branch=checkout_branch,
                    checkout_pr=checkout_pr,
                    dry_run=dry_run,
                    only_clean=only_clean,
                    height=height,
                    width=width,
                )
                if not confirmed_rows:
                    _dialog_msgbox("Smart Sync", "No repositories confirmed.", height, width)
                    continue

                selected_repos = [str(r.get("repo") or "") for r in confirmed_rows if str(r.get("repo") or "")]
                if selected_repos:
                    apply_cmd.extend(["--repos", ",".join(selected_repos)])
                log_path = _fleet_log_path(session["root"])
                apply_cmd.extend(["--log-json", log_path])

                _dialog_infobox(
                    "Smart Sync",
                    "Applying fleet actions...\n\nProgress is shown in terminal.",
                    max(8, height // 3),
                    max(60, width // 2),
                )
                result = _run_lantern_subprocess(apply_cmd, height, width, capture=False)
                subprocess.run(["clear"], check=False)
                summary_text = _fleet_short_summary_from_log(log_path)
                if result.returncode == 0:
                    _dialog_msgbox(
                        "Smart Sync",
                        summary_text,
                        height,
                        width,
                    )
                else:
                    _dialog_msgbox(
                        "Smart Sync",
                        f"{summary_text}\n\nSmart Sync finished with errors.",
                        height,
                        width,
                    )
                continue

            if fleet_action == "plan":
                fetch = _dialog_yesno("Fetch", "Run local git fetch before building fleet plan?")
                include_prs = _dialog_yesno("PR Info", "Include fresh open PR numbers/branches in plan?")
                plan_opts = list(common_opts)
                if fetch:
                    plan_opts.append("--fetch")
                if include_prs:
                    plan_opts.append("--with-prs")
                _dialog_infobox(
                    "Fleet Plan",
                    "Building fleet plan...\n\nThis may take a while for large workspaces.",
                    max(8, height // 3),
                    max(60, width // 2),
                )
                result = _run_lantern_subprocess(
                    [sys.executable, "-m", "lantern", "fleet", "plan", *plan_opts],
                    height,
                    width,
                )
                subprocess.run(["clear"], check=False)
                if result.returncode == 0 and result.stdout:
                    _dialog_textbox_from_text("Fleet Plan", result.stdout, height, width)
                continue

            fetch = _dialog_yesno("Fetch", "Run local git fetch before loading fleet context?")
            include_prs = _dialog_yesno("PR Info", "Include fresh open PR numbers/branches in context?")
            apply_common_opts = list(common_opts)
            if fetch:
                apply_common_opts.append("--fetch")
            _dialog_infobox(
                "Fleet Apply",
                "Loading repositories, branches and PR context...\n\nPlease wait.",
                max(8, height // 3),
                max(60, width // 2),
            )
            plan_args = argparse.Namespace(
                root=session["root"],
                max_depth=session["max_depth"],
                include_hidden=session["include_hidden"],
                fetch=fetch,
                server=server,
                input="",
                user="",
                token="",
                include_forks=session["include_forks"],
                with_prs=include_prs,
                pr_stale_days=30,
            )
            try:
                rows, _meta = _fleet_plan_records(plan_args)
            except Exception as exc:
                subprocess.run(["clear"], check=False)
                _dialog_msgbox("Error", str(exc), height, width)
                continue
            subprocess.run(["clear"], check=False)

            actionable = [r for r in rows if r.get("action") in {"clone", "pull", "push"}]
            if not actionable:
                _dialog_msgbox("Fleet", "No actionable repositories in current plan.", height, width)
                continue

            preview_cols = ["repo", "state", "action", "latest_branch", "prs"]
            _dialog_textbox_from_text("Fleet Context", render_table(actionable, preview_cols), height, width)

            selected_rows = actionable
            if fleet_action == "apply_select":
                checklist_items: List[Tuple[str, str, bool]] = []
                idx_to_repo: Dict[str, str] = {}
                for i, row in enumerate(actionable, start=1):
                    tag = str(i)
                    repo_name = str(row.get("repo") or "")
                    idx_to_repo[tag] = repo_name
                    prs = str(row.get("prs") or "-")
                    latest = str(row.get("latest_branch") or "-")
                    desc = f"{repo_name} [{row.get('state')}] -> {row.get('action')} | latest:{latest} | prs:{prs}"
                    checklist_items.append((tag, desc, True))
                selected_tags = _dialog_checklist("Fleet Apply", "Select repos to process:", checklist_items, height, width)
                if not selected_tags:
                    continue
                selected_repos = {idx_to_repo[tag] for tag in selected_tags if tag in idx_to_repo}
                if not selected_repos:
                    continue
                selected_rows = [r for r in actionable if str(r.get("repo") or "") in selected_repos]

            mode_items: List[Tuple[str, str]] = [
                ("sync", "Sync only (clone/pull/push)"),
                ("pr", "Checkout PR number on selected repos"),
                ("branch", "Checkout branch on selected repos"),
            ]
            apply_mode = _dialog_menu("Apply Mode", "Choose apply mode:", mode_items, height, width)
            if not apply_mode:
                continue

            checkout_pr = ""
            checkout_branch = ""
            if apply_mode == "pr":
                pr_numbers: List[str] = []
                for row in selected_rows:
                    for part in str(row.get("prs") or "").split(","):
                        p = part.strip()
                        if p and p != "-" and p not in pr_numbers:
                            pr_numbers.append(p)
                hint = ", ".join(pr_numbers[:20]) if pr_numbers else "No fresh PR numbers detected in selected repos."
                checkout_pr = (
                    _dialog_inputbox(
                        "Checkout PR",
                        f"Enter PR number to checkout on selected repos.\n\nDetected PR numbers: {hint}",
                        pr_numbers[0] if pr_numbers else "",
                    )
                    or ""
                ).strip()
                if not checkout_pr:
                    continue
            elif apply_mode == "branch":
                branch_hints: List[str] = []
                for row in selected_rows:
                    b = str(row.get("latest_branch") or "").strip()
                    if b and b != "-" and b not in branch_hints:
                        branch_hints.append(b)
                hint = ", ".join(branch_hints[:20]) if branch_hints else "No latest branch hints detected."
                checkout_branch = (
                    _dialog_inputbox(
                        "Checkout Branch",
                        f"Enter branch to checkout/update on selected repos.\n\nLatest branch hints: {hint}",
                        branch_hints[0] if branch_hints else "",
                    )
                    or ""
                ).strip()
                if not checkout_branch:
                    continue

            dry_run = _dialog_yesno("Dry Run", "Perform a dry run (no changes)?")
            only_clean = _dialog_yesno("Only Clean", "Skip dirty repos for pull/push?")
            push_choice = _dialog_menu(
                "Push Mode",
                "Should ahead repositories be pushed to remote?",
                [
                    ("no_push", "No push (skip ahead repos)"),
                    ("push", "Push ahead repos to remote"),
                ],
                height,
                width,
            )
            if not push_choice:
                continue
            include_push = push_choice == "push"
            apply_cmd = [
                sys.executable, "-m", "lantern", "fleet", "apply",
                *apply_common_opts,
                "--clone-missing", "--pull-behind",
            ]
            if include_push:
                apply_cmd.append("--push-ahead")
            if checkout_pr:
                apply_cmd.extend(["--checkout-pr", checkout_pr])
            if checkout_branch:
                apply_cmd.extend(["--checkout-branch", checkout_branch])
            if dry_run:
                apply_cmd.append("--dry-run")
            if only_clean:
                apply_cmd.append("--only-clean")

            confirmed_rows = _fleet_preflight_confirm(
                title="Fleet Apply Plan",
                rows=selected_rows,
                clone_missing=True,
                pull_behind=True,
                push_ahead=include_push,
                checkout_branch=checkout_branch,
                checkout_pr=checkout_pr,
                dry_run=dry_run,
                only_clean=only_clean,
                height=height,
                width=width,
            )
            if not confirmed_rows:
                _dialog_msgbox("Fleet", "No repositories confirmed.", height, width)
                continue

            selected_repos = [str(r.get("repo") or "") for r in confirmed_rows if str(r.get("repo") or "")]
            if selected_repos:
                apply_cmd.extend(["--repos", ",".join(selected_repos)])
            log_path = _fleet_log_path(session["root"])
            apply_cmd.extend(["--log-json", log_path])

            _dialog_infobox(
                "Fleet Sync",
                "Applying selected fleet actions...\n\nThis may take a while.",
                max(8, height // 3),
                max(60, width // 2),
            )
            # Stream progress live for long-running fleet apply operations.
            result = _run_lantern_subprocess(apply_cmd, height, width, capture=False)
            subprocess.run(["clear"], check=False)
            summary_text = _fleet_short_summary_from_log(log_path)
            if result.returncode == 0:
                _dialog_msgbox(
                    "Fleet Sync",
                    summary_text,
                    height,
                    width,
                )
            else:
                _dialog_msgbox(
                    "Fleet Sync",
                    f"{summary_text}\n\nFleet apply finished with errors.",
                    height,
                    width,
                )

        elif action == "lazygit":
            if not _validate_session_root(session["root"], height, width):
                continue
            binary = _lazygit_path()
            if not binary:
                _dialog_msgbox(
                    "lazygit",
                    "lazygit is not installed.\n\nInstall it and try again.",
                    height,
                    width,
                )
                continue
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            if not repos_list:
                _dialog_msgbox("lazygit", f"No repositories found in:\n{session['root']}", height, width)
                continue
            items = []
            for idx, path in enumerate(repos_list, start=1):
                tag = str(idx)
                desc = f"{os.path.basename(path)} -> {path}"
                items.append((tag, desc))
            selected = _dialog_menu("lazygit", "Select repository to open:", items, height, width)
            if not selected:
                continue
            try:
                selected_idx = int(selected) - 1
            except ValueError:
                continue
            if selected_idx < 0 or selected_idx >= len(repos_list):
                continue
            _launch_lazygit(repos_list[selected_idx])

        elif action == "scan":
            if not _validate_session_root(session["root"], height, width):
                continue
            output = session["scan_path"]
            fetch = _dialog_yesno("Fetch", "Run 'git fetch' before scanning?")
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            _dialog_infobox(
                "Scan",
                "Scanning repositories...\n\nPlease wait.",
                max(8, height // 3),
                max(60, width // 2),
            )
            records = _collect_repo_records_with_progress(repos_list, fetch, "scan")
            subprocess.run(["clear"], check=False)
            payload = {"root": session["root"], "repos": records}
            output_dir = os.path.dirname(output)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            try:
                with open(output, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                _dialog_msgbox("Scan", f"Scan complete. Found {len(records)} repositories.\n\nOutput saved to:\n{output}")
            except OSError as exc:
                _dialog_msgbox("Error", f"Failed to write output: {exc}")

        elif action == "table":
            input_file = session["scan_path"]
            if not os.path.isfile(input_file):
                if _dialog_yesno(
                    "Scan File Missing",
                    f"Scan file not found:\n{input_file}\n\nRun a scan now?",
                    height,
                    width,
                ):
                    fetch = _dialog_yesno("Fetch", "Run 'git fetch' before scanning?")
                    repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
                    _dialog_infobox(
                        "Scan",
                        "Scanning repositories...\n\nPlease wait.",
                        max(8, height // 3),
                        max(60, width // 2),
                    )
                    records = _collect_repo_records_with_progress(repos_list, fetch, "scan")
                    subprocess.run(["clear"], check=False)
                    payload = {"root": session["root"], "repos": records}
                    output_dir = os.path.dirname(input_file)
                    if output_dir:
                        os.makedirs(output_dir, exist_ok=True)
                    try:
                        with open(input_file, "w", encoding="utf-8") as handle:
                            json.dump(payload, handle, indent=2)
                    except OSError as exc:
                        _dialog_msgbox("Error", f"Failed to write scan file: {exc}")
                        continue
                else:
                    _dialog_msgbox("Error", f"File not found: {input_file}")
                    continue
            if not os.path.isfile(input_file):
                continue
            try:
                with open(input_file, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                records = payload.get("repos", [])
                if not records:
                    _dialog_msgbox("Table", "No records found in the JSON file.")
                    continue
                # Add divergence fields if present
                for record in records:
                    if all(key in record for key in ("up_ahead", "up_behind", "main_ahead", "main_behind")):
                        add_divergence_fields(record)
                records = _sort_records_by_repo_name(records)
                # Determine columns
                if all(key in records[0] for key in ("name", "branch", "upstream", "up", "main_ref", "main")):
                    columns = ["name", "branch", "upstream", "up", "main_ref", "main"]
                else:
                    columns = list(records[0].keys())
                output_text = render_table(records, columns)
                _dialog_textbox_from_text("Table", output_text, height, width)
            except (json.JSONDecodeError, OSError) as exc:
                _dialog_msgbox("Error", f"Failed to read JSON file: {exc}")

        elif action == "sync":
            if not _validate_session_root(session["root"], height, width):
                continue
            sync_items: List[Tuple[str, str]] = [
                ("fetch", "Fetch only"),
                ("pull", "Fetch and pull"),
                ("push", "Fetch, pull, and push"),
            ]
            sync_action = _dialog_menu("Sync", "Select sync operation:", sync_items)
            if sync_action:
                dry_run = _dialog_yesno("Dry Run", "Perform a dry run (no actual changes)?")
                only_clean = _dialog_yesno("Only Clean", "Skip repos with uncommitted changes?")
                only_upstream = _dialog_yesno("Only Upstream", "Skip repos without an upstream branch?")
                cmd_args = [sys.executable, "-m", "lantern", "sync", "--root", session["root"],
                            "--max-depth", str(session["max_depth"])]
                if session["include_hidden"]:
                    cmd_args.append("--include-hidden")
                if sync_action == "fetch":
                    cmd_args.append("--fetch")
                elif sync_action == "pull":
                    cmd_args.extend(["--fetch", "--pull"])
                elif sync_action == "push":
                    cmd_args.extend(["--fetch", "--pull", "--push"])
                if dry_run:
                    cmd_args.append("--dry-run")
                if only_clean:
                    cmd_args.append("--only-clean")
                if only_upstream:
                    cmd_args.append("--only-upstream")
                result = _run_lantern_subprocess(cmd_args, height, width)
                if result.returncode == 0 and result.stdout:
                    _dialog_textbox_from_text("Sync Results", result.stdout, height, width)
                elif result.returncode == 0:
                    _dialog_msgbox("Sync", "No repositories found.")

        elif action == "find":
            if not _validate_session_root(session["root"], height, width):
                continue
            name = _dialog_inputbox("Name Filter", "Filter by repository name (leave empty for all):", "")
            remote = _dialog_inputbox("Remote Filter", "Filter by remote URL (leave empty for all):", "")
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            records = []
            for path in repos_list:
                repo_name = os.path.basename(path)
                origin = git.get_origin_url(path)
                if name and name not in repo_name:
                    continue
                if remote and (not origin or remote not in origin):
                    continue
                records.append({"name": repo_name, "path": path, "origin": origin})
            if records:
                records = _sort_records_by_repo_name(records)
                output_text = render_table(records, ["name", "path", "origin"])
                _dialog_textbox_from_text("Find Results", output_text, height, width)
            else:
                _dialog_msgbox("Find", "No repositories found matching the filters.")

        elif action == "duplicates":
            if not _validate_session_root(session["root"], height, width):
                continue
            repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
            groups: Dict[str, List[str]] = {}
            for path in repos_list:
                origin = git.get_origin_url(path)
                if not origin:
                    continue
                groups.setdefault(origin, []).append(path)
            records = []
            for origin, paths in sorted(groups.items()):
                if len(paths) < 2:
                    continue
                records.append({
                    "origin": origin,
                    "paths": " | ".join(sorted(paths)),
                    "count": str(len(paths)),
                })
            if records:
                output_text = render_table(records, ["count", "origin", "paths"])
                _dialog_textbox_from_text("Duplicates", output_text, height, width)
            else:
                _dialog_msgbox("Duplicates", "No duplicate repositories found.")

        elif action == "forge":
            forge_items: List[Tuple[str, str]] = [
                ("list", "List remote repositories (display)"),
                ("list_file", "List remote repositories (save to file)"),
                ("clone", "Clone repositories from list"),
                ("snippets", "List gists/snippets (display)"),
                ("snippets_file", "List gists/snippets (save to file)"),
                ("snippet_dl", "Download a gist/snippet"),
                ("gist_create", "Create a gist (GitHub only)"),
                ("back", "Back to main menu"),
            ]
            forge_action = _dialog_menu("Forge Operations", "Select an operation:", forge_items, height, width)

            if forge_action in ("list", "list_file"):
                config = lantern_config.load_config()
                server_records = lantern_config.list_servers(config)
                if not server_records:
                    _dialog_msgbox("Error", "No servers configured.\n\nUse 'config' > 'setup' to add servers.")
                    continue
                server_items = [(rec["name"], rec["provider"]) for rec in server_records]
                server = _dialog_menu("Select Server", "Choose a server:", server_items)
                if server:
                    cmd_args = [sys.executable, "-m", "lantern", "forge", "list", "--server", server]
                    if session["include_forks"]:
                        cmd_args.append("--include-forks")
                    if forge_action == "list_file":
                        output = _dialog_inputbox("Output File", "Enter output JSON file path:", "data/github.json")
                        if not output:
                            continue
                        cmd_args.extend(["--output", output])
                    result = _run_lantern_subprocess(cmd_args, height, width)
                    if result.returncode == 0:
                        if forge_action == "list_file":
                            _dialog_msgbox("List", f"Repository list saved to:\n{output}")
                        elif result.stdout:
                            _dialog_textbox_from_text(f"Repositories on {server}", result.stdout, height, width)

            elif forge_action == "clone":
                input_file = _resolve_existing_repo_list_file(session["root"])
                if not input_file:
                    config = lantern_config.load_config()
                    server_records = lantern_config.list_servers(config)
                    if not server_records:
                        _dialog_msgbox(
                            "Error",
                            "No servers configured.\n\nUse 'config' > 'setup' to add servers first.",
                            height,
                            width,
                        )
                        continue
                    default_server = lantern_config.get_server_name(config, "")
                    server = ""
                    if default_server and any(rec["name"] == default_server for rec in server_records):
                        server = default_server
                    elif len(server_records) == 1:
                        server = server_records[0]["name"]
                    else:
                        server_items = [(rec["name"], rec["provider"]) for rec in server_records]
                        chosen = _dialog_menu("Select Server", "Choose a server to build repository list:", server_items, height, width)
                        if not chosen:
                            continue
                        server = chosen
                    safe_server = server.replace("/", "_")
                    input_file = os.path.join(session["root"], "data", f"{safe_server}.json")
                    cmd_args = [sys.executable, "-m", "lantern", "forge", "list", "--server", server, "--output", input_file]
                    if session["include_forks"]:
                        cmd_args.append("--include-forks")
                    result = _run_lantern_subprocess(cmd_args, height, width)
                    if result.returncode != 0 or not os.path.isfile(input_file):
                        _dialog_msgbox(
                            "Error",
                            "Could not generate repository list JSON automatically.\n"
                            "Check server config and try again.",
                            height,
                            width,
                        )
                        continue
                clone_root = _dialog_inputbox("Clone Directory", "Enter directory to clone into:", session["root"])
                if clone_root:
                    cmd_args = [sys.executable, "-m", "lantern", "forge", "clone", "--input", input_file, "--root", clone_root, "--tui"]
                    _run_lantern_subprocess(cmd_args, height, width, capture=False)

            elif forge_action in ("snippets", "snippets_file"):
                config = lantern_config.load_config()
                server_records = lantern_config.list_servers(config)
                if not server_records:
                    _dialog_msgbox("Error", "No servers configured.\n\nUse 'config' > 'setup' to add servers.")
                    continue
                server_items = [(rec["name"], rec["provider"]) for rec in server_records]
                server = _dialog_menu("Select Server", "Choose a server:", server_items)
                if server:
                    cmd_args = [sys.executable, "-m", "lantern", "forge", "snippets", "list", "--server", server]
                    if forge_action == "snippets_file":
                        output = _dialog_inputbox("Output File", "Enter output JSON file path:", "data/snippets.json")
                        if not output:
                            continue
                        cmd_args.extend(["--output", output])
                    result = _run_lantern_subprocess(cmd_args, height, width)
                    if result.returncode == 0:
                        if forge_action == "snippets_file":
                            _dialog_msgbox("Snippets", f"Snippet list saved to:\n{output}")
                        elif result.stdout:
                            _dialog_textbox_from_text(f"Gists/Snippets on {server}", result.stdout, height, width)

            elif forge_action == "snippet_dl":
                config = lantern_config.load_config()
                server_records = lantern_config.list_servers(config)
                if not server_records:
                    _dialog_msgbox("Error", "No servers configured.\n\nUse 'config' > 'setup' to add servers.")
                    continue
                server_items = [(rec["name"], rec["provider"]) for rec in server_records]
                server = _dialog_menu("Select Server", "Choose a server:", server_items)
                if not server:
                    continue
                snippet_id = _dialog_inputbox("Snippet ID", "Enter the gist/snippet ID:")
                if not snippet_id:
                    continue
                output_dir = _dialog_inputbox("Output Directory", "Enter directory to save files:", ".")
                if not output_dir:
                    continue
                cmd_args = [sys.executable, "-m", "lantern", "forge", "snippets", "clone",
                            snippet_id, "--server", server, "--output-dir", output_dir, "--force"]
                result = _run_lantern_subprocess(cmd_args, height, width)
                if result.returncode == 0 and result.stdout:
                    _dialog_msgbox("Download", result.stdout.strip())

            elif forge_action == "gist_create":
                config = lantern_config.load_config()
                server_records = lantern_config.list_servers(config)
                github_servers = [rec for rec in server_records if rec.get("provider") == "github"]
                if not github_servers:
                    _dialog_msgbox("Error", "No GitHub servers configured.\n\nGist creation requires a GitHub server.")
                    continue
                server_items = [(rec["name"], rec["provider"]) for rec in github_servers]
                server = _dialog_menu("Select Server", "Choose a GitHub server:", server_items)
                if not server:
                    continue
                file_path = _dialog_inputbox("File", "Enter path to the file to upload:")
                if not file_path:
                    continue
                if not os.path.isfile(file_path):
                    _dialog_msgbox("Error", f"File not found: {file_path}")
                    continue
                description = _dialog_inputbox("Description", "Enter gist description (optional):", "") or ""
                is_public = _dialog_yesno("Visibility", "Make this gist public?")
                cmd_args = [sys.executable, "-m", "lantern", "forge", "gists", "create",
                            "--server", server, "--file", file_path]
                if description:
                    cmd_args.extend(["--description", description])
                if is_public:
                    cmd_args.append("--public")
                else:
                    cmd_args.append("--private")
                result = _run_lantern_subprocess(cmd_args, height, width)
                if result.returncode == 0 and result.stdout:
                    _dialog_msgbox("Created", f"Gist created:\n{result.stdout.strip()}")

        elif action == "report":
            input_file = session["scan_path"]
            if not os.path.isfile(input_file):
                if _dialog_yesno(
                    "Scan File Missing",
                    f"Scan file not found:\n{input_file}\n\nRun a scan now?",
                    height,
                    width,
                ):
                    fetch = _dialog_yesno("Fetch", "Run 'git fetch' before scanning?")
                    repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
                    _dialog_infobox(
                        "Scan",
                        "Scanning repositories...\n\nPlease wait.",
                        max(8, height // 3),
                        max(60, width // 2),
                    )
                    records = _collect_repo_records_with_progress(repos_list, fetch, "scan")
                    subprocess.run(["clear"], check=False)
                    payload = {"root": session["root"], "repos": records}
                    output_dir = os.path.dirname(input_file)
                    if output_dir:
                        os.makedirs(output_dir, exist_ok=True)
                    try:
                        with open(input_file, "w", encoding="utf-8") as handle:
                            json.dump(payload, handle, indent=2)
                    except OSError as exc:
                        _dialog_msgbox("Error", f"Failed to write scan file: {exc}")
                        continue
                else:
                    _dialog_msgbox("Error", f"File not found: {input_file}")
                    continue
            if not os.path.isfile(input_file):
                continue
            fmt_items: List[Tuple[str, str]] = [
                ("csv", "Comma-separated values"),
                ("json", "JSON format"),
                ("md", "Markdown table"),
            ]
            fmt = _dialog_menu("Format", "Select export format:", fmt_items, height, width)
            if not fmt:
                continue
            # Ask for column selection
            try:
                with open(input_file, "r", encoding="utf-8") as handle:
                    peek = json.load(handle)
                all_records = peek.get("repos", [])
                if all_records:
                    available_cols = list(all_records[0].keys())
                else:
                    available_cols = []
            except (json.JSONDecodeError, OSError):
                available_cols = []
            columns_str = ""
            if available_cols:
                cols_text = ", ".join(available_cols)
                columns_str = _dialog_inputbox(
                    "Columns",
                    f"Enter comma-separated columns (leave empty for all):\n\nAvailable: {cols_text}",
                    "",
                ) or ""
            output = _dialog_inputbox("Output File", "Enter output file path (leave empty for display):", "")
            cmd_args = [sys.executable, "-m", "lantern", "report", "--input", input_file, "--format", fmt]
            if columns_str:
                cmd_args.extend(["--columns", columns_str])
            if output:
                cmd_args.extend(["--output", output])
            result = _run_lantern_subprocess(cmd_args, height, width)
            if result.returncode == 0:
                if output:
                    _dialog_msgbox("Report", f"Report exported to:\n{output}")
                elif result.stdout:
                    _dialog_textbox_from_text("Report", result.stdout, height, width)

        elif action == "command":
            raw = _dialog_inputbox("Run Command", "Enter lantern arguments (without leading 'lantern'):", "")
            if raw is None:
                continue
            argv = shlex.split(raw)
            if not argv:
                _dialog_msgbox("Command", "No command entered.", height, width)
                continue
            cmd_args = [sys.executable, "-m", "lantern", *argv]
            result = _run_lantern_subprocess(cmd_args, height, width)
            if result.returncode == 0 and result.stdout:
                _dialog_textbox_from_text("Command Output", result.stdout, height, width)
            elif result.returncode == 0:
                _dialog_msgbox("Command", "Command completed with no output.", height, width)

    return 0


def cmd_repos(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    records = []
    for path in repos:
        records.append(
            {
                "name": os.path.basename(path),
                "path": path,
                "origin": git.get_origin_url(path),
            }
        )
    records = _sort_records_by_repo_name(records)
    columns = ["name", "path", "origin"]
    print(render_table(records, columns))
    return 0


def _lazygit_candidates(root: str, max_depth: int, include_hidden: bool) -> List[Dict[str, str]]:
    repos = find_repos(root, max_depth, include_hidden)
    out: List[Dict[str, str]] = []
    for path in repos:
        out.append(
            {
                "name": os.path.basename(path),
                "path": path,
                "origin": git.get_origin_url(path) or "-",
            }
        )
    return _sort_records_by_repo_name(out)


def cmd_lazygit(args: argparse.Namespace) -> int:
    if not _lazygit_path():
        print("lazygit is not installed or not in PATH.", file=sys.stderr)
        return 1

    target_path = ""
    if args.path:
        target_path = os.path.abspath(args.path)
        if not os.path.isdir(target_path) or not _is_git_repo_root(target_path):
            print(f"Not a git repository root: {target_path}", file=sys.stderr)
            return 1
    else:
        candidates = _lazygit_candidates(args.root, args.max_depth, args.include_hidden)
        if args.repo:
            matches = [r for r in candidates if r["name"] == args.repo]
            if len(matches) == 1:
                target_path = matches[0]["path"]
            elif not matches:
                print(f"Repository not found under root: {args.repo}", file=sys.stderr)
                return 1
            else:
                print(f"Multiple repositories named '{args.repo}'. Use --path.", file=sys.stderr)
                print(render_table(matches, ["name", "path", "origin"]))
                return 1
        elif _is_git_repo_root(os.getcwd()):
            target_path = os.getcwd()
        elif len(candidates) == 1:
            target_path = candidates[0]["path"]
        elif args.select and _dialog_available():
            items = []
            for idx, rec in enumerate(candidates, start=1):
                items.append((str(idx), f"{rec['name']} -> {rec['path']}"))
            selected = _dialog_menu("lazygit", "Select repository to open:", items)
            if not selected:
                return 0
            try:
                selected_idx = int(selected) - 1
            except ValueError:
                return 1
            if selected_idx < 0 or selected_idx >= len(candidates):
                return 1
            target_path = candidates[selected_idx]["path"]
        else:
            if not candidates:
                print(f"No repositories found under root: {args.root}", file=sys.stderr)
                return 1
            print(
                "Multiple repositories found. Use --repo <name>, --path <repo-path>, or --select for selection.",
                file=sys.stderr,
            )
            print(render_table(candidates, ["name", "path", "origin"]))
            return 1

    return _launch_lazygit(target_path)


def cmd_scan(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    records = _collect_repo_records_with_progress(repos, args.fetch, "scan")
    output = {"root": args.root, "repos": records}

    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
    else:
        json.dump(output, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    records = [add_divergence_fields(record) for record in _collect_repo_records_with_progress(repos, args.fetch, "status")]
    records = _sort_records_by_repo_name(records)
    columns = [
        "name",
        "branch",
        "upstream",
        "up",
        "main_ref",
        "main",
    ]
    print(render_table(records, columns))
    return 0


def _normalize_repo_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("git@") and ":" in raw:
        host_part, path_part = raw[4:].split(":", 1)
        host = host_part.strip().lower()
        path = path_part.strip().lower()
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host}/{path}"
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/").lower()
    if path.endswith(".git"):
        path = path[:-4]
    if host and path:
        return f"{host}/{path}"
    return raw.lower()


def _origin_owner_repo(origin_url: str) -> Tuple[str, str, str]:
    raw = (origin_url or "").strip()
    if not raw:
        return "", "", ""
    if raw.startswith("git@") and ":" in raw:
        host_part, path_part = raw[4:].split(":", 1)
        host = host_part.strip().lower()
        parts = [p for p in path_part.strip("/").split("/") if p]
        if len(parts) < 2:
            return host, "", ""
        owner = parts[-2]
        repo = parts[-1]
    else:
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "").lower()
        parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
        if len(parts) < 2:
            return host, "", ""
        owner = parts[-2]
        repo = parts[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return host, owner, repo


def _remote_repo_keys(repo: Dict[str, Any]) -> Set[str]:
    keys: Set[str] = set()
    for field in ("ssh_url", "clone_url", "html_url"):
        value = str(repo.get(field) or "").strip()
        normalized = _normalize_repo_url(value)
        if normalized:
            keys.add(normalized)
    return keys


def _fleet_server_context(args: argparse.Namespace) -> Tuple[str, str, str, str, Optional[Dict[str, str]], Dict[str, Any]]:
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = str(server.get("base_url") or "")
    env_user_key = f"{provider.upper()}_USER"
    env_token_key = f"{provider.upper()}_TOKEN"
    user = str(args.user or env.get(env_user_key) or server.get("user") or "")
    token = str(args.token or env.get(env_token_key) or server.get("token") or "")
    auth = server.get("auth") if isinstance(server.get("auth"), dict) else None
    return provider, base_url, user, token, auth, server


def _fleet_load_remote(args: argparse.Namespace) -> Dict[str, Any]:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {"repos": []}

    provider, base_url, user, token, auth, server = _fleet_server_context(args)
    repos = forge.fetch_repos(provider, user, token, args.include_forks, base_url, auth)
    return {
        "server": server.get("name", provider),
        "provider": provider,
        "base_url": base_url or forge.DEFAULT_BASE_URLS.get(provider, ""),
        "user": user,
        "repos": repos,
    }


def _fleet_plan_records(args: argparse.Namespace) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    local_paths = find_repos(args.root, args.max_depth, args.include_hidden)
    local_records: List[Dict[str, str]] = []
    total_local = len(local_paths)
    for idx, path in enumerate(local_paths, start=1):
        _progress_line(idx, total_local, f"Scanning {os.path.basename(path)}")
        record = build_repo_record(path, args.fetch)
        record = add_divergence_fields(record)
        record["clean"] = "yes" if git.is_clean(path) else "no"
        local_records.append(record)
    _progress_done()

    payload = _fleet_load_remote(args)
    provider, base_url, _user, token, _auth, _server = _fleet_server_context(args)
    remote_repos = payload.get("repos", []) if isinstance(payload.get("repos"), list) else []
    remote_by_key: Dict[str, Dict[str, Any]] = {}
    for repo in remote_repos:
        if not isinstance(repo, dict):
            continue
        for key in _remote_repo_keys(repo):
            remote_by_key[key] = repo

    local_by_remote_key: Dict[str, Dict[str, str]] = {}
    pr_cache: Dict[str, List[Dict[str, Any]]] = {}
    include_prs = bool(getattr(args, "with_prs", False))
    stale_days = int(getattr(args, "pr_stale_days", 30) or 30)
    plan_rows: List[Dict[str, str]] = []
    for rec in local_records:
        path = rec.get("path", "")
        origin = str(rec.get("origin") or "")
        origin_key = _normalize_repo_url(origin)
        remote_repo = remote_by_key.get(origin_key) if origin_key else None
        if remote_repo and origin_key:
            local_by_remote_key[origin_key] = rec
        up_ahead = _to_int_or_none(rec.get("up_ahead"))
        up_behind = _to_int_or_none(rec.get("up_behind"))
        state = "local-only"
        action = "-"
        if remote_repo:
            if (up_ahead or 0) > 0 and (up_behind or 0) > 0:
                state = "diverged"
                action = "manual"
            elif (up_behind or 0) > 0:
                state = "behind-remote"
                action = "pull"
            elif (up_ahead or 0) > 0:
                state = "ahead-remote"
                action = "push"
            else:
                state = "in-sync"
                action = "-"
        latest_branch = "-"
        prs = "-"
        if include_prs and provider == "github":
            host, owner, repo_name = _origin_owner_repo(origin)
            if owner and repo_name:
                cache_key = f"{owner}/{repo_name}"
                if cache_key not in pr_cache:
                    try:
                        pr_cache[cache_key] = github.fetch_open_pull_requests(
                            owner=owner,
                            repo=repo_name,
                            token=token or None,
                            stale_days=stale_days,
                            base_url=base_url or None,
                        )
                    except Exception:
                        pr_cache[cache_key] = []
                repo_prs = pr_cache.get(cache_key, [])
                if repo_prs:
                    latest_branch = str(repo_prs[0].get("head_ref") or "-")
                    prs = ",".join(str(pr.get("number")) for pr in repo_prs[:8] if pr.get("number") is not None) or "-"
        plan_rows.append(
            {
                "repo": rec.get("name", os.path.basename(path)),
                "state": state,
                "up": str(rec.get("up") or "-"),
                "clean": str(rec.get("clean") or "no"),
                "action": action,
                "latest_branch": latest_branch,
                "prs": prs,
                "path": path,
            }
        )

    for repo in remote_repos:
        if not isinstance(repo, dict):
            continue
        keys = _remote_repo_keys(repo)
        if not keys or any(k in local_by_remote_key for k in keys):
            continue
        name = str(repo.get("name") or "").strip()
        if not name:
            continue
        dest = os.path.join(args.root, name)
        plan_rows.append(
            {
                "repo": name,
                "state": "missing-local",
                "up": "-",
                "clean": "-",
                "action": "clone",
                "latest_branch": "-",
                "prs": "-",
                "path": dest,
            }
        )

    plan_rows.sort(key=lambda r: (r["repo"].lower(), r["state"]))
    metadata = {"server": str(payload.get("server") or ""), "remote_count": len(remote_repos), "local_count": len(local_records)}
    return plan_rows, metadata


def _parse_repo_filter(raw: str) -> Set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def cmd_fleet_plan(args: argparse.Namespace) -> int:
    print("Building fleet plan... please wait.", file=sys.stderr)
    try:
        rows, meta = _fleet_plan_records(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    columns = ["repo", "state", "up", "clean", "action"]
    if getattr(args, "with_prs", False):
        columns.extend(["latest_branch", "prs"])
    columns.append("path")
    print(render_table(rows, columns))
    if rows:
        print(
            f"\nserver={meta.get('server') or '-'} local={meta.get('local_count', 0)} "
            f"remote={meta.get('remote_count', 0)} total={len(rows)}"
        )
    print(f"Fleet plan ready: {len(rows)} rows.", file=sys.stderr)
    return 0


def cmd_fleet_apply(args: argparse.Namespace) -> int:
    checkout_branch = str(getattr(args, "checkout_branch", "") or "").strip()
    checkout_pr = str(getattr(args, "checkout_pr", "") or "").strip()
    pr_number = 0
    if checkout_pr:
        try:
            pr_number = int(checkout_pr)
        except ValueError:
            print(f"Invalid --checkout-pr value: {checkout_pr}", file=sys.stderr)
            return 1
    if checkout_branch.startswith("origin/"):
        checkout_branch = checkout_branch.split("/", 1)[1]

    if not (args.clone_missing or args.pull_behind or args.push_ahead or checkout_branch or pr_number):
        args.clone_missing = True
        args.pull_behind = True
        args.push_ahead = True

    provider = base_url = user = token = ""
    try:
        payload = _fleet_load_remote(args)
        provider, base_url, user, token, _auth, _server = _fleet_server_context(args)
        rows, _meta = _fleet_plan_records(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    clone_sources: Dict[str, str] = {}
    for remote_repo in payload.get("repos", []):
        if not isinstance(remote_repo, dict):
            continue
        name = str(remote_repo.get("name") or "").strip()
        if not name:
            continue
        src = str(remote_repo.get("ssh_url") or remote_repo.get("clone_url") or "").strip()
        if src:
            clone_sources[name] = src

    selected = _parse_repo_filter(args.repos)
    target_rows = [row for row in rows if not selected or row["repo"] in selected]
    results: List[Dict[str, str]] = []
    detailed_results: List[Dict[str, Any]] = []
    total_targets = len(target_rows)
    for idx, row in enumerate(target_rows, start=1):
        repo = row["repo"]
        state = row["state"]
        path = row["path"]
        _progress_line(idx, total_targets, f"fleet-apply: {repo} [{state}]")
        statuses: List[str] = []
        action_records: List[Dict[str, str]] = []
        planned_actions = _fleet_action_parts_for_row(
            row=row,
            clone_missing=args.clone_missing,
            pull_behind=args.pull_behind,
            push_ahead=args.push_ahead,
            checkout_branch=checkout_branch,
            checkout_pr=checkout_pr,
        )
        clone_ok = state != "missing-local"
        if state == "missing-local" and args.clone_missing:
            if args.dry_run:
                statuses.append("clone:dry-run")
                action_records.append({"action": "clone", "status": "dry-run"})
                clone_ok = False
            else:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                clone_src = clone_sources.get(repo, "")
                if not clone_src:
                    statuses.append("clone:missing-url")
                    action_records.append({"action": "clone", "status": "missing-url"})
                    clone_ok = False
                else:
                    proc = subprocess.run(["git", "clone", clone_src, path], check=False)
                    ok = proc.returncode == 0
                    statuses.append(f"clone:{'ok' if ok else 'fail'}")
                    action_records.append({"action": "clone", "status": "ok" if ok else "fail"})
                    clone_ok = ok
        elif state == "behind-remote" and args.pull_behind:
            if args.only_clean and row.get("clean") != "yes":
                statuses.append("pull:skip-dirty")
                action_records.append({"action": "pull", "status": "skip-dirty"})
            elif args.dry_run:
                statuses.append("pull:dry-run")
                action_records.append({"action": "pull", "status": "dry-run"})
            else:
                proc = subprocess.run(
                    ["git", "-C", path, "pull", "--ff-only"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                ok = proc.returncode == 0
                statuses.append(f"pull:{'ok' if ok else 'fail'}")
                action_records.append({"action": "pull", "status": "ok" if ok else "fail"})
        elif state == "ahead-remote" and args.push_ahead:
            if args.only_clean and row.get("clean") != "yes":
                statuses.append("push:skip-dirty")
                action_records.append({"action": "push", "status": "skip-dirty"})
            elif args.dry_run:
                statuses.append("push:dry-run")
                action_records.append({"action": "push", "status": "dry-run"})
            else:
                proc = subprocess.run(
                    ["git", "-C", path, "push"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                ok = proc.returncode == 0
                statuses.append(f"push:{'ok' if ok else 'fail'}")
                action_records.append({"action": "push", "status": "ok" if ok else "fail"})

        effective_branch = checkout_branch
        if pr_number:
            _host, owner, repo_name = _origin_owner_repo(str(git.get_origin_url(path) or ""))
            if provider == "github" and owner and repo_name:
                try:
                    resolved = github.get_pr_branch(
                        owner=owner,
                        repo=repo_name,
                        pr_number=pr_number,
                        token=token or None,
                        base_url=base_url or None,
                    )
                except Exception:
                    resolved = None
                if resolved:
                    effective_branch = resolved
                else:
                    statuses.append(f"checkout-pr:{pr_number}:not-found")
                    action_records.append({"action": "checkout-pr", "status": "not-found", "pr": str(pr_number)})
            else:
                statuses.append(f"checkout-pr:{pr_number}:unsupported")
                action_records.append({"action": "checkout-pr", "status": "unsupported", "pr": str(pr_number)})

        if effective_branch:
            if args.only_clean and row.get("clean") != "yes":
                statuses.append(f"checkout:{effective_branch}:skip-dirty")
                action_records.append({"action": "checkout", "status": "skip-dirty", "branch": effective_branch})
            elif not clone_ok and not args.dry_run:
                statuses.append(f"checkout:{effective_branch}:skip-not-cloned")
                action_records.append({"action": "checkout", "status": "skip-not-cloned", "branch": effective_branch})
            elif args.dry_run:
                statuses.append(f"checkout:{effective_branch}:dry-run")
                action_records.append({"action": "checkout", "status": "dry-run", "branch": effective_branch})
            else:
                _run_git_op(path, ["fetch", "--prune"])
                remote_ref = f"origin/{effective_branch}"
                has_remote = bool(git.run_git(path, ["rev-parse", "--verify", remote_ref]))
                if not has_remote:
                    statuses.append(f"checkout:{effective_branch}:skip-no-remote")
                    action_records.append({"action": "checkout", "status": "skip-no-remote", "branch": effective_branch})
                else:
                    has_local = bool(git.run_git(path, ["rev-parse", "--verify", effective_branch]))
                    rc_checkout = _run_git_op(path, ["checkout", effective_branch]) if has_local else _run_git_op(path, ["checkout", "-b", effective_branch, "--track", remote_ref])
                    rc_pull = _run_git_op(path, ["pull", "--ff-only"]) if rc_checkout == 0 else 1
                    ok = rc_checkout == 0 and rc_pull == 0
                    statuses.append(f"checkout:{effective_branch}:{'ok' if ok else 'fail'}")
                    action_records.append({"action": "checkout", "status": "ok" if ok else "fail", "branch": effective_branch})

        if not statuses:
            statuses = ["skip"]
            action_records.append({"action": "skip", "status": "none"})
        results.append({"repo": repo, "state": state, "result": " ".join(statuses), "path": path})
        detailed_results.append(
            {
                "repo": repo,
                "state": state,
                "path": path,
                "clean": str(row.get("clean") or "-"),
                "planned_actions": planned_actions,
                "actions": action_records,
                "result": " ".join(statuses),
            }
        )
    _progress_done()

    if not results:
        print("No repositories selected.")
        return 0
    results = _sort_records_by_repo_name(results)
    print(render_table(results, ["repo", "state", "result", "path"]))
    if args.log_json:
        action_totals: Dict[str, int] = {}
        updated_repos = 0
        branch_updates: List[Dict[str, str]] = []
        for rec in detailed_results:
            actions = rec.get("actions", [])
            if not isinstance(actions, list):
                continue
            changed = False
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_name = str(action.get("action") or "")
                status = str(action.get("status") or "")
                key = f"{action_name}:{status}"
                action_totals[key] = action_totals.get(key, 0) + 1
                if status in {"ok", "dry-run"} and action_name in {"clone", "pull", "push", "checkout"}:
                    changed = True
                if action_name == "checkout" and status in {"ok", "dry-run"} and action.get("branch"):
                    branch_updates.append({"repo": str(rec.get("repo") or ""), "branch": str(action.get("branch") or "")})
            if changed:
                updated_repos += 1
        log_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "command": "fleet apply",
            "options": {
                "root": args.root,
                "server": args.server,
                "repos": args.repos,
                "clone_missing": bool(args.clone_missing),
                "pull_behind": bool(args.pull_behind),
                "push_ahead": bool(args.push_ahead),
                "checkout_branch": checkout_branch,
                "checkout_pr": checkout_pr,
                "dry_run": bool(args.dry_run),
                "only_clean": bool(args.only_clean),
                "fetch": bool(args.fetch),
                "include_hidden": bool(args.include_hidden),
                "max_depth": int(args.max_depth),
            },
            "summary": {
                "repos_targeted": len(target_rows),
                "repos_processed": len(detailed_results),
                "repos_updated": updated_repos,
                "branch_updates": len(branch_updates),
                "action_totals": action_totals,
            },
            "branch_updates": branch_updates,
            "results": detailed_results,
        }
        log_dir = os.path.dirname(args.log_json)
        try:
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(args.log_json, "w", encoding="utf-8") as handle:
                json.dump(log_payload, handle, indent=2)
        except OSError as exc:
            print(f"Failed to write fleet log '{args.log_json}': {exc}", file=sys.stderr)
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("repos", [])
    if not records:
        print("No records.")
        return 0
    for record in records:
        if not all(
            key in record for key in ("up_ahead", "up_behind", "main_ahead", "main_behind")
        ):
            continue
        add_divergence_fields(record)
    records = _sort_records_by_repo_name(records)
    if args.columns:
        columns = args.columns.split(",")
    else:
        if all(
            key in records[0]
            for key in ("name", "branch", "upstream", "up", "main_ref", "main")
        ):
            columns = ["name", "branch", "upstream", "up", "main_ref", "main"]
        else:
            columns = list(records[0].keys())
    print(render_table(records, columns))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    records = []
    for path in repos:
        name = os.path.basename(path)
        origin = git.get_origin_url(path)
        if args.name and args.name not in name:
            continue
        if args.remote and (not origin or args.remote not in origin):
            continue
        records.append({"name": name, "path": path, "origin": origin})
    records = _sort_records_by_repo_name(records)
    columns = ["name", "path", "origin"]
    print(render_table(records, columns))
    return 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    groups: Dict[str, List[str]] = {}
    for path in repos:
        origin = git.get_origin_url(path)
        if not origin:
            continue
        groups.setdefault(origin, []).append(path)

    records = []
    for origin, paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        records.append(
            {
                "origin": origin,
                "paths": " | ".join(sorted(paths)),
                "count": str(len(paths)),
            }
        )

    columns = ["count", "origin", "paths"]
    print(render_table(records, columns))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    actions: List[Tuple[str, List[str]]] = []
    if not (args.fetch or args.pull or args.push):
        args.fetch = True
    if args.fetch:
        actions.append(("fetch", ["fetch", "--prune"]))
    if args.pull:
        actions.append(("pull", ["pull", "--ff-only"]))
    if args.push:
        actions.append(("push", ["push"]))

    records = []
    total_steps = max(1, len(repos) * max(1, len(actions)))
    current_step = 0
    for path in repos:
        name = os.path.basename(path)
        if args.only_clean and not git.is_clean(path):
            records.append({"name": name, "path": path, "result": "skip:dirty"})
            continue
        if args.only_upstream and not git.get_upstream(path):
            records.append({"name": name, "path": path, "result": "skip:no-upstream"})
            continue
        statuses = []
        for label, cmd in actions:
            current_step += 1
            _progress_line(current_step, total_steps, f"sync: {label} {name}")
            if args.dry_run:
                statuses.append(f"{label}:dry-run")
                continue
            result = subprocess.run(
                ["git", "-C", path, *cmd],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            statuses.append(f"{label}:{'ok' if result.returncode == 0 else 'fail'}")
        records.append({"name": name, "path": path, "result": " ".join(statuses)})
    _progress_done()

    records = _sort_records_by_repo_name(records)
    columns = ["name", "result", "path"]
    print(render_table(records, columns))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("repos", [])
    if not records:
        print("No records.")
        return 0
    records = _sort_records_by_repo_name(records)
    if args.format == "json":
        if args.columns:
            fields = args.columns.split(",")
            filtered = [{field: record.get(field) for field in fields} for record in records]
        else:
            filtered = records
        output = {"root": payload.get("root"), "repos": filtered}
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(output, handle, indent=2)
        else:
            json.dump(output, sys.stdout, indent=2)
            sys.stdout.write("\n")
        return 0
    if args.format == "md":
        columns = args.columns.split(",") if args.columns else list(records[0].keys())
        lines = []
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for record in records:
            row = [str(record.get(col, "")) for col in columns]
            lines.append("| " + " | ".join(row) + " |")
        output = "\n".join(lines)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(output + "\n")
        else:
            print(output)
        return 0

    import csv

    fields = args.columns.split(",") if args.columns else list(records[0].keys())
    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        handle = open(args.output, "w", encoding="utf-8", newline="")
        close_handle = True
    else:
        handle = sys.stdout
        close_handle = False
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for record in records:
        writer.writerow({field: record.get(field, "") for field in fields})
    if close_handle:
        handle.close()
    return 0


def cmd_servers(args: argparse.Namespace) -> int:
    config = lantern_config.load_config()
    records = lantern_config.list_servers(config)
    if not records:
        print("No servers configured.")
        return 0
    columns = ["name", "provider", "base_url", "user"]
    print(render_table(records, columns))
    return 0


def _normalize_servers(value: object) -> Dict[str, Dict]:
    if not isinstance(value, dict):
        return {}
    servers: Dict[str, Dict] = {}
    for name, server in value.items():
        if isinstance(server, dict):
            servers[str(name)] = dict(server)
    return servers


def _redact_server_secrets(servers: Dict[str, Dict]) -> Dict[str, Dict]:
    redacted: Dict[str, Dict] = {}
    for name, server in servers.items():
        cleaned = dict(server)
        cleaned.pop("token", None)
        cleaned.pop("TOKEN", None)
        redacted[name] = cleaned
    return redacted


def _has_server_secrets(servers: Dict[str, Dict]) -> bool:
    for server in servers.values():
        if "token" in server or "TOKEN" in server:
            return True
    return False


def _write_json_secure(path: str, payload: Dict) -> None:
    if os.path.islink(path):
        raise OSError(f"Refusing to write to symlink path: {path}")
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".lantern.", dir=target_dir)
    try:
        # Best-effort permission tightening on the temporary file; guard for platforms
        # where os.fchmod is unavailable (e.g., Windows) or unsupported filesystems.
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Best-effort permission tightening; ignore unsupported filesystems.
            pass
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            # Best-effort cleanup of temporary file; ignore failures deleting tmp_path.
            pass


def cmd_config_export(args: argparse.Namespace) -> int:
    config = lantern_config.load_config()
    servers = _normalize_servers(config.get("servers", {}))
    includes_secrets = args.include_secrets
    had_secrets = _has_server_secrets(servers)
    if not includes_secrets:
        servers = _redact_server_secrets(servers)
    payload = {
        "default_server": config.get("default_server", ""),
        "servers": servers,
    }
    if had_secrets and not includes_secrets:
        print("Redacted secrets from export. Use --include-secrets to include tokens.", file=sys.stderr)
    output_path = args.output or "git-lantern-servers.json"
    if output_path == "-":
        if includes_secrets:
            print(
                "Refusing to write secrets to stdout. Use --output <path> or omit --include-secrets.",
                file=sys.stderr,
            )
            return 1
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if includes_secrets:
        try:
            _write_json_secure(output_path, payload)
        except OSError as exc:
            print(f"Failed to write secure config export: {exc}", file=sys.stderr)
            return 1
    else:
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    print(f"Wrote {output_path}")
    return 0


def cmd_config_import(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    incoming_servers = _normalize_servers(payload.get("servers", {}))
    incoming_default = payload.get("default_server", "")
    if args.replace:
        merged = {"servers": incoming_servers}
    else:
        merged = lantern_config.load_config()
        current_servers = _normalize_servers(merged.get("servers", {}))
        current_servers.update(incoming_servers)
        merged["servers"] = current_servers
    if incoming_default:
        merged["default_server"] = incoming_default
    output_path = args.output or lantern_config.config_path()
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    try:
        _write_json_secure(output_path, merged)
    except OSError as exc:
        print(f"Failed to write secure config: {exc}", file=sys.stderr)
        return 1
    print(f"Updated {output_path}")
    return 0


def cmd_config_path(_: argparse.Namespace) -> int:
    print(lantern_config.config_path())
    return 0


def _format_list_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _render_list_table(records: List[Dict[str, object]], columns: List[str]) -> None:
    if not records:
        print("No records.")
        return
    display_records: List[Dict[str, str]] = []
    for record in records:
        display_records.append({col: _format_list_value(record.get(col)) for col in columns})
    if "repo" in columns or "name" in columns:
        display_records = _sort_records_by_repo_name(display_records)
    print(render_table(display_records, columns))


def _safe_output_path(output_dir: str, name: str) -> Optional[str]:
    normalized = os.path.normpath(name)
    drive, _ = os.path.splitdrive(normalized)
    if drive:
        return None
    if os.path.isabs(normalized) or normalized in (".", "..") or normalized.startswith(".." + os.sep):
        return None
    abs_output_dir = os.path.abspath(output_dir)
    dest = os.path.join(abs_output_dir, normalized)
    abs_dest = os.path.abspath(dest)
    try:
        if os.path.commonpath([abs_output_dir, abs_dest]) != abs_output_dir:
            return None
    except ValueError:
        return None
    dest_dir = os.path.dirname(abs_dest)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    return abs_dest


def cmd_github_list(args: argparse.Namespace) -> int:
    if args.output == "":
        print(
            "Output path cannot be empty. Use --output - for stdout or omit --output to render a table.",
            file=sys.stderr,
        )
        return 1
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    env_user_key = f"{provider.upper()}_USER"
    env_token_key = f"{provider.upper()}_TOKEN"
    user = args.user or env.get(env_user_key) or server.get("user")
    token = args.token or env.get(env_token_key) or server.get("token")
    auth = server.get("auth") if isinstance(server.get("auth"), dict) else None
    try:
        repos = forge.fetch_repos(provider, user, token, args.include_forks, base_url, auth)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    repos = _sort_records_by_repo_name(repos)
    payload = {
        "server": server.get("name", provider),
        "provider": provider,
        "base_url": base_url or forge.DEFAULT_BASE_URLS.get(provider, ""),
        "user": user,
        "repos": repos,
    }
    if args.output:
        if args.output == "-":
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return 0
    if args.output is None:
        columns = ["name", "private", "default_branch", "ssh_url", "html_url"]
        _render_list_table(repos, columns)
    return 0


def cmd_github_clone(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if args.server:
        config = lantern_config.load_config()
        server = lantern_config.get_server(config, args.server)
        expected = server.get("name", "")
        expected_provider = server.get("provider", "")
        expected_base = (server.get("base_url") or "").rstrip("/")
        payload_server = payload.get("server", "")
        payload_provider = payload.get("provider", "")
        payload_base = (payload.get("base_url") or "").rstrip("/")
        if payload_server and expected and payload_server != expected:
            print(
                f"Input server '{payload_server}' does not match requested '{expected}'.",
                file=sys.stderr,
            )
            return 1
        if payload_provider and expected_provider and payload_provider != expected_provider:
            print(
                f"Input provider '{payload_provider}' does not match requested '{expected_provider}'.",
                file=sys.stderr,
            )
            return 1
        if payload_base and expected_base and payload_base != expected_base:
            print(
                f"Input base_url '{payload_base}' does not match requested '{expected_base}'.",
                file=sys.stderr,
            )
            return 1
    repos = payload.get("repos", [])
    repos = _sort_records_by_repo_name(repos)
    os.makedirs(args.root, exist_ok=True)
    if args.tui:
        if not shutil.which("dialog"):
            print("dialog is required for --tui.", file=sys.stderr)
            return 1
        checklist_items = []
        for repo in repos:
            name = repo.get("name")
            if not name:
                continue
            dest = os.path.join(args.root, name)
            status = "on" if os.path.exists(dest) else "off"
            label = "cloned" if status == "on" else "missing"
            checklist_items.extend([name, label, status])
        if not checklist_items:
            print("No repositories available to clone.", file=sys.stderr)
            return 1
        dialog_cmd = [
            "dialog",
            "--stdout",
            "--separate-output",
            "--title",
            "Forge Clone",
            "--checklist",
            "Select repos to clone (existing repos are pre-checked).",
            "25",
            "120",
            "18",
            *checklist_items,
        ]
        result = subprocess.run(dialog_cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            print("No repositories selected.")
            return 0
        selected = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if not selected:
            print("No repositories selected.")
            return 0
        selected_set = set(selected)
        repos = [repo for repo in repos if repo.get("name") in selected_set]
        repos = _sort_records_by_repo_name(repos)
    for repo in repos:
        name = repo.get("name")
        ssh_url = repo.get("ssh_url")
        if not name or not ssh_url:
            continue
        dest = os.path.join(args.root, name)
        if os.path.exists(dest):
            continue
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if args.dry_run:
            print(f"[DRY RUN] git clone {ssh_url} {dest}")
            continue
        subprocess.run(["git", "clone", ssh_url, dest], check=False)
    return 0


def cmd_github_gists_list(args: argparse.Namespace) -> int:
    if args.output == "":
        print(
            "Output path cannot be empty. Use --output - for stdout or omit --output to render a table.",
            file=sys.stderr,
        )
        return 1
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    if provider != "github":
        print("Gists are only supported for GitHub servers.", file=sys.stderr)
        return 1
    user = args.user or env.get("GITHUB_USER") or server.get("user")
    token = args.token or env.get("GITHUB_TOKEN") or server.get("token")
    try:
        gists = github.fetch_gists(user, token, base_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = {"user": user, "gists": gists}
    if args.output:
        if args.output == "-":
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return 0
    if args.output is None:
        columns = ["id", "description", "public", "files", "updated_at"]
        _render_list_table(gists, columns)
    return 0


def cmd_github_gists_clone(args: argparse.Namespace) -> int:
    gist_id = args.gist_id
    if args.input and os.path.isfile(args.input):
        with open(args.input, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        gists = payload.get("gists", [])
        if gists and not any(gist.get("id") == gist_id for gist in gists):
            print(
                f"Gist id not found in input list: {gist_id}",
                file=sys.stderr,
            )
            return 1

    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    if provider != "github":
        print("Gists are only supported for GitHub servers.", file=sys.stderr)
        return 1
    token = args.token or env.get("GITHUB_TOKEN") or server.get("token")

    gist_detail = github.get_gist(gist_id, token, base_url)
    files = gist_detail.get("files") or {}
    if not files:
        print("Gist has no files.", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    requested = args.file
    if requested:
        names = requested
    else:
        names = list(files.keys())
        if len(names) > 1:
            print("Gist has multiple files; use --file to select.", file=sys.stderr)
            return 1

    for name in names:
        info = files.get(name)
        if not info:
            print(f"File not found in gist: {name}", file=sys.stderr)
            return 1
        raw_url = info.get("raw_url")
        if not raw_url:
            print(f"Missing raw_url for file: {name}", file=sys.stderr)
            return 1
        try:
            content = github.download_gist_file(raw_url, token, base_url)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        dest = _safe_output_path(output_dir, name)
        if not dest:
            print(f"Refusing to write file with unsafe path: {name}", file=sys.stderr)
            return 1
        if os.path.exists(dest) and not args.force:
            print(f"File exists: {dest} (use --force to overwrite)", file=sys.stderr)
            return 1
        with open(dest, "wb") as handle:
            handle.write(content)
        print(f"Wrote {dest}")
    return 0


def cmd_forge_snippets_list(args: argparse.Namespace) -> int:
    if args.output == "":
        print(
            "Output path cannot be empty. Use --output - for stdout or omit --output to render a table.",
            file=sys.stderr,
        )
        return 1
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    env_user_key = f"{provider.upper()}_USER"
    env_token_key = f"{provider.upper()}_TOKEN"
    user = args.user or env.get(env_user_key) or server.get("user")
    token = args.token or env.get(env_token_key) or server.get("token")
    auth = server.get("auth") if isinstance(server.get("auth"), dict) else None
    try:
        snippets = forge.fetch_snippets(provider, user, token, base_url, auth)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "server": server.get("name", provider),
        "provider": provider,
        "base_url": base_url or forge.DEFAULT_BASE_URLS.get(provider, ""),
        "user": user,
        "snippets": snippets,
    }
    if args.output:
        if args.output == "-":
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return 0
    if args.output is None:
        display_rows: List[Dict[str, object]] = []
        for snippet in snippets:
            row = dict(snippet)
            if provider == "github":
                row["visibility"] = "public" if row.get("public") else "secret"
                row["title"] = row.get("title") or ""
                row["description"] = row.get("description") or ""
            display_rows.append(row)
        columns = ["id", "title", "description", "visibility", "files", "updated_at"]
        _render_list_table(display_rows, columns)
    return 0


def cmd_forge_snippets_clone(args: argparse.Namespace) -> int:
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    env_user_key = f"{provider.upper()}_USER"
    env_token_key = f"{provider.upper()}_TOKEN"
    user = args.user or env.get(env_user_key) or server.get("user")
    token = args.token or env.get(env_token_key) or server.get("token")
    auth = server.get("auth") if isinstance(server.get("auth"), dict) else None

    snippet_id = args.snippet_id
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    if args.input and os.path.isfile(args.input):
        with open(args.input, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        listed = payload.get("snippets", [])
        if listed and not any(item.get("id") == snippet_id for item in listed):
            print(f"Snippet id not found in input list: {snippet_id}", file=sys.stderr)
            return 1

    if provider == "github":
        gist_detail = github.get_gist(snippet_id, token, base_url)
        files = gist_detail.get("files") or {}
        if not files:
            print("Snippet has no files.", file=sys.stderr)
            return 1
        requested = args.file
        if requested:
            names = requested
        else:
            names = list(files.keys())
            if len(names) > 1:
                print("Snippet has multiple files; use --file to select.", file=sys.stderr)
                return 1
        for name in names:
            info = files.get(name)
            if not info:
                print(f"File not found in snippet: {name}", file=sys.stderr)
                return 1
            raw_url = info.get("raw_url")
            if not raw_url:
                print(f"Missing raw_url for file: {name}", file=sys.stderr)
                return 1
            try:
                content = github.download_gist_file(raw_url, token, base_url)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            dest = _safe_output_path(output_dir, name)
            if not dest:
                print(f"Refusing to write file with unsafe path: {name}", file=sys.stderr)
                return 1
            if os.path.exists(dest) and not args.force:
                print(f"File exists: {dest} (use --force to overwrite)", file=sys.stderr)
                return 1
            with open(dest, "wb") as handle:
                handle.write(content)
            print(f"Wrote {dest}")
        return 0

    if provider == "gitlab":
        if not token:
            print("Token is required for GitLab snippets.", file=sys.stderr)
            return 1
        detail = forge.get_gitlab_snippet(str(snippet_id), token, base_url, auth)
        files: List[Dict[str, str]] = []
        if isinstance(detail.get("files"), list):
            files = detail.get("files")  # type: ignore[assignment]
        else:
            file_name = detail.get("file_name")
            raw_url = detail.get("raw_url")
            if file_name and raw_url:
                files = [{"path": file_name, "raw_url": raw_url}]
        if not files:
            print("Snippet has no files.", file=sys.stderr)
            return 1
        requested = args.file
        if requested:
            names = requested
        else:
            names = [item.get("path") or item.get("file_name") or "" for item in files]
            names = [name for name in names if name]
            if len(names) > 1:
                print("Snippet has multiple files; use --file to select.", file=sys.stderr)
                return 1
        for name in names:
            match = next(
                (
                    item
                    for item in files
                    if item.get("path") == name or item.get("file_name") == name
                ),
                None,
            )
            if not match:
                print(f"File not found in snippet: {name}", file=sys.stderr)
                return 1
            raw_url = match.get("raw_url")
            if not raw_url:
                print(f"Missing raw_url for file: {name}", file=sys.stderr)
                return 1
            headers = forge.auth_headers("gitlab", user, token, auth)
            try:
                content = forge.download_with_headers(raw_url, headers, base_url)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            dest = _safe_output_path(output_dir, name)
            if not dest:
                print(f"Refusing to write file with unsafe path: {name}", file=sys.stderr)
                return 1
            if os.path.exists(dest) and not args.force:
                print(f"File exists: {dest} (use --force to overwrite)", file=sys.stderr)
                return 1
            with open(dest, "wb") as handle:
                handle.write(content)
            print(f"Wrote {dest}")
        return 0

    if provider == "bitbucket":
        if not user:
            print("Workspace is required for Bitbucket snippets.", file=sys.stderr)
            return 1
        detail = forge.get_bitbucket_snippet(user, str(snippet_id), token, base_url, auth)
        files_map = detail.get("files")
        file_names: List[str] = []
        if isinstance(files_map, dict):
            file_names = list(files_map.keys())
        if not file_names and not args.file:
            print("Snippet has no file list; use --file to select.", file=sys.stderr)
            return 1
        names = args.file or file_names
        base_api = (base_url or forge.DEFAULT_BASE_URLS.get(provider, "")).rstrip("/")
        headers = forge.auth_headers("bitbucket", user, token, auth)
        for name in names:
            raw_url = (
                f"{base_api}/snippets/{urllib.parse.quote(user)}"
                f"/{urllib.parse.quote(str(snippet_id))}/files/{urllib.parse.quote(name)}"
            )
            try:
                content = forge.download_with_headers(raw_url, headers, base_url)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            dest = _safe_output_path(output_dir, name)
            if not dest:
                print(f"Refusing to write file with unsafe path: {name}", file=sys.stderr)
                return 1
            if os.path.exists(dest) and not args.force:
                print(f"File exists: {dest} (use --force to overwrite)", file=sys.stderr)
                return 1
            with open(dest, "wb") as handle:
                handle.write(content)
            print(f"Wrote {dest}")
        return 0

    print(f"Unsupported provider: {provider}", file=sys.stderr)
    return 1

def cmd_github_gists_update(args: argparse.Namespace) -> int:
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    if provider != "github":
        print("Gists are only supported for GitHub servers.", file=sys.stderr)
        return 1
    token = args.token or env.get("GITHUB_TOKEN") or server.get("token")
    if not token:
        print("GitHub token is required to update gists.", file=sys.stderr)
        return 1

    files: Dict[str, str] = {}
    for file_arg in args.file:
        if "=" in file_arg:
            name, path = file_arg.split("=", 1)
        else:
            path = file_arg
            name = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as handle:
            files[name] = handle.read()

    delete_names = args.delete

    if not files and not delete_names:
        print("At least one --file or --delete is required.", file=sys.stderr)
        return 1

    if not args.force:
        current = github.get_gist(args.gist_id, token, base_url)
        existing = set((current.get("files") or {}).keys())
        overlap = existing.intersection(files.keys())
        delete_overlap = existing.intersection(delete_names)
        if overlap or delete_overlap:
            print(
                "Refusing to overwrite/delete existing files without --force.",
                file=sys.stderr,
            )
            return 1

    update_files: Dict[str, Optional[str]] = {}
    for name, content in files.items():
        update_files[name] = content
    for name in delete_names:
        update_files[name] = None

    github.update_gist(args.gist_id, token, update_files, args.description, base_url)
    print("Gist updated.")
    return 0


def cmd_github_gists_create(args: argparse.Namespace) -> int:
    env = github.load_env()
    config = lantern_config.load_config()
    server = lantern_config.get_server(config, args.server)
    provider = (server.get("provider") or "github").lower()
    base_url = server.get("base_url", "")
    if provider != "github":
        print("Gists are only supported for GitHub servers.", file=sys.stderr)
        return 1
    token = args.token or env.get("GITHUB_TOKEN") or server.get("token")
    if not token:
        print("GitHub token is required to create gists.", file=sys.stderr)
        return 1

    files: Dict[str, str] = {}
    for file_arg in args.file:
        if "=" in file_arg:
            name, path = file_arg.split("=", 1)
        else:
            path = file_arg
            name = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as handle:
            files[name] = handle.read()

    if not files:
        print("At least one --file is required.", file=sys.stderr)
        return 1

    if args.private:
        is_public = False
    elif args.public:
        is_public = True
    else:
        is_public = False
    created = github.create_gist(token, files, args.description, is_public, base_url)
    print(created.get("html_url", "Gist created."))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lantern")
    parser.add_argument(
        "--root",
        default="",
        help="workspace root override for TUI session (stored root is used when omitted)",
    )
    parser.add_argument(
        "--tui", "-t",
        action="store_true",
        help="Launch interactive TUI mode using dialog",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    servers = sub.add_parser("servers", help="list configured git servers")
    servers.set_defaults(func=cmd_servers)

    config = sub.add_parser("config", help="import/export server configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    config_export = config_sub.add_parser("export", help="export server config to JSON")
    config_export.add_argument("--output", default="git-lantern-servers.json")
    config_export.add_argument(
        "--include-secrets",
        action="store_true",
        help="include tokens in export (writes file with restricted permissions)",
    )
    config_export.set_defaults(func=cmd_config_export)

    config_import = config_sub.add_parser("import", help="import server config from JSON")
    config_import.add_argument("--input", default="git-lantern-servers.json")
    config_import.add_argument("--output", default="")
    config_import.add_argument("--replace", action="store_true")
    config_import.set_defaults(func=cmd_config_import)

    config_path = config_sub.add_parser("path", help="print active config path")
    config_path.set_defaults(func=cmd_config_path)

    config_setup = config_sub.add_parser("setup", help="interactive server configuration (TUI)")
    config_setup.set_defaults(func=cmd_config_setup)

    repos = sub.add_parser("repos", help="list local repos")
    repos.add_argument("--root", default=os.getcwd())
    repos.add_argument("--max-depth", type=int, default=6)
    repos.add_argument("--include-hidden", action="store_true")
    repos.set_defaults(func=cmd_repos)

    lazygit = sub.add_parser("lazygit", help="open lazygit for a selected repository")
    lazygit.add_argument("--root", default=os.getcwd())
    lazygit.add_argument("--max-depth", type=int, default=6)
    lazygit.add_argument("--include-hidden", action="store_true")
    lazygit.add_argument("--repo", default="", help="repository directory name under root")
    lazygit.add_argument("--path", default="", help="explicit repository path")
    lazygit.add_argument("--select", action="store_true", help="interactive repo selection with dialog")
    lazygit.set_defaults(func=cmd_lazygit)

    scan = sub.add_parser("scan", help="scan for git repos and write JSON")
    scan.add_argument("--root", default=os.getcwd())
    scan.add_argument("--output", default="data/repos.json")
    scan.add_argument("--max-depth", type=int, default=6)
    scan.add_argument("--include-hidden", action="store_true")
    scan.add_argument("--fetch", action="store_true")
    scan.set_defaults(func=cmd_scan)

    status = sub.add_parser("status", help="scan for git repos and render a table")
    status.add_argument("--root", default=os.getcwd())
    status.add_argument("--max-depth", type=int, default=6)
    status.add_argument("--include-hidden", action="store_true")
    status.add_argument("--fetch", action="store_true")
    status.set_defaults(func=cmd_status)

    table = sub.add_parser("table", help="render a table from a JSON scan")
    table.add_argument("--input", default="data/repos.json")
    table.add_argument("--columns", default="")
    table.set_defaults(func=cmd_table)

    find = sub.add_parser("find", help="find repos by name or remote")
    find.add_argument("--root", default=os.getcwd())
    find.add_argument("--max-depth", type=int, default=6)
    find.add_argument("--include-hidden", action="store_true")
    find.add_argument("--name", default="")
    find.add_argument("--remote", default="")
    find.set_defaults(func=cmd_find)

    dupes = sub.add_parser("duplicates", help="find repos with the same origin URL")
    dupes.add_argument("--root", default=os.getcwd())
    dupes.add_argument("--max-depth", type=int, default=6)
    dupes.add_argument("--include-hidden", action="store_true")
    dupes.set_defaults(func=cmd_duplicates)

    sync = sub.add_parser("sync", help="fetch/pull/push across repos")
    sync.add_argument("--root", default=os.getcwd())
    sync.add_argument("--max-depth", type=int, default=6)
    sync.add_argument("--include-hidden", action="store_true")
    sync.add_argument("--fetch", action="store_true")
    sync.add_argument("--pull", action="store_true")
    sync.add_argument("--push", action="store_true")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--only-clean", action="store_true")
    sync.add_argument("--only-upstream", action="store_true")
    sync.set_defaults(func=cmd_sync)

    fleet = sub.add_parser("fleet", help="unified multi-repo management (plan/apply)")
    fleet_sub = fleet.add_subparsers(dest="fleet_command", required=True)

    fleet_plan = fleet_sub.add_parser("plan", help="show local vs remote reconciliation plan")
    fleet_plan.add_argument("--root", default=os.getcwd())
    fleet_plan.add_argument("--max-depth", type=int, default=6)
    fleet_plan.add_argument("--include-hidden", action="store_true")
    fleet_plan.add_argument("--fetch", action="store_true")
    fleet_plan.add_argument("--server", default="")
    fleet_plan.add_argument("--input", default="")
    fleet_plan.add_argument("--user", default="")
    fleet_plan.add_argument("--token", default="")
    fleet_plan.add_argument("--include-forks", action="store_true")
    fleet_plan.add_argument("--with-prs", action="store_true", help="include fresh open PR numbers/branches (GitHub)")
    fleet_plan.add_argument("--pr-stale-days", type=int, default=30, help="exclude PRs older than this number of days")
    fleet_plan.set_defaults(func=cmd_fleet_plan)

    fleet_apply = fleet_sub.add_parser("apply", help="apply clone/pull/push actions from fleet plan")
    fleet_apply.add_argument("--root", default=os.getcwd())
    fleet_apply.add_argument("--max-depth", type=int, default=6)
    fleet_apply.add_argument("--include-hidden", action="store_true")
    fleet_apply.add_argument("--fetch", action="store_true")
    fleet_apply.add_argument("--server", default="")
    fleet_apply.add_argument("--input", default="")
    fleet_apply.add_argument("--user", default="")
    fleet_apply.add_argument("--token", default="")
    fleet_apply.add_argument("--include-forks", action="store_true")
    fleet_apply.add_argument("--repos", default="", help="comma-separated repo names to target")
    fleet_apply.add_argument("--clone-missing", action="store_true")
    fleet_apply.add_argument("--pull-behind", action="store_true")
    fleet_apply.add_argument("--push-ahead", action="store_true")
    fleet_apply.add_argument("--checkout-branch", default="", help="checkout/update this branch on selected repos (tracks origin/<branch>)")
    fleet_apply.add_argument("--checkout-pr", default="", help="checkout branch for this PR number (GitHub)")
    fleet_apply.add_argument("--dry-run", action="store_true")
    fleet_apply.add_argument("--only-clean", action="store_true")
    fleet_apply.add_argument("--log-json", default="", help="write full fleet apply execution log to JSON")
    fleet_apply.set_defaults(func=cmd_fleet_apply)

    fleet_logs = fleet_sub.add_parser("logs", help="inspect fleet apply JSON logs")
    fleet_logs.add_argument("--root", default=os.getcwd())
    fleet_logs.add_argument("--input", default="", help="explicit fleet log JSON path")
    fleet_logs.add_argument("--latest", action="store_true", help="show the latest fleet log")
    fleet_logs.add_argument("--limit", type=int, default=20, help="row limit for table output")
    fleet_logs.add_argument("--show-results", action="store_true", help="include per-repo result table in fallback output")
    fleet_logs.add_argument("--no-pretty", action="store_true", help="disable jq pretty JSON output and use tabular summary output")
    fleet_logs.set_defaults(func=cmd_fleet_logs)

    report = sub.add_parser("report", help="export scan results")
    report.add_argument("--input", default="data/repos.json")
    report.add_argument("--output", default="")
    report.add_argument("--format", choices=["csv", "json", "md"], default="csv")
    report.add_argument("--columns", default="")
    report.set_defaults(func=cmd_report)

    forge = sub.add_parser("forge", help="git server utilities")
    forge_sub = forge.add_subparsers(dest="forge_command", required=True)

    gh_list = forge_sub.add_parser("list", help="list repos (table or JSON)")
    gh_list.add_argument("--server", default="")
    gh_list.add_argument("--user", default="")
    gh_list.add_argument("--token", default="")
    gh_list.add_argument("--include-forks", action="store_true")
    gh_list.add_argument(
        "--output",
        default=None,
        help="Output JSON file (use - for stdout). Omit to render a table.",
    )
    gh_list.set_defaults(func=cmd_github_list)

    gh_clone = forge_sub.add_parser("clone", help="clone missing repos from JSON list")
    gh_clone.add_argument("--server", default="")
    gh_clone.add_argument("--input", default="data/github.json")
    gh_clone.add_argument("--root", default=os.getcwd())
    gh_clone.add_argument("--dry-run", action="store_true")
    gh_clone.add_argument("--tui", action="store_true")
    gh_clone.set_defaults(func=cmd_github_clone)

    gh_gists = forge_sub.add_parser("gists", help="GitHub gists utilities")
    gh_gists_sub = gh_gists.add_subparsers(dest="gists_command", required=True)
    gh_gist = forge_sub.add_parser("gist", help="GitHub gists utilities")
    gh_gist_sub = gh_gist.add_subparsers(dest="gists_command", required=True)
    gh_snippets = forge_sub.add_parser("snippets", help="GitHub snippets utilities")
    gh_snippets_sub = gh_snippets.add_subparsers(dest="gists_command", required=True)
    gh_snippet = forge_sub.add_parser("snippet", help="GitHub snippets utilities")
    gh_snippet_sub = gh_snippet.add_subparsers(dest="gists_command", required=True)

    gh_gists_list = gh_gists_sub.add_parser("list", help="list gists to JSON")
    gh_gist_list = gh_gist_sub.add_parser("list", help="list gists to JSON")
    gh_snippets_list = gh_snippets_sub.add_parser("list", help="list snippets to JSON")
    gh_snippet_list = gh_snippet_sub.add_parser("list", help="list snippets to JSON")
    for parser_item in (
        gh_gists_list,
        gh_gist_list,
    ):
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--user", default="")
        parser_item.add_argument("--token", default="")
        parser_item.add_argument("--output", default=None)
        parser_item.set_defaults(func=cmd_github_gists_list)
    for parser_item in (
        gh_snippets_list,
        gh_snippet_list,
    ):
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--user", default="")
        parser_item.add_argument("--token", default="")
        parser_item.add_argument("--output", default=None)
        parser_item.set_defaults(func=cmd_forge_snippets_list)

    gh_gists_clone = gh_gists_sub.add_parser("clone", help="download gist files")
    gh_gist_clone = gh_gist_sub.add_parser("clone", help="download gist files")
    gh_snippets_clone = gh_snippets_sub.add_parser("clone", help="download snippet files")
    gh_snippet_clone = gh_snippet_sub.add_parser("clone", help="download snippet files")
    for parser_item in (
        gh_gists_clone,
        gh_gist_clone,
    ):
        parser_item.add_argument("gist_id", help="gist id from list output")
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--token", default="")
        parser_item.add_argument("--input", default="data/gists.json")
        parser_item.add_argument("--output-dir", default=".")
        parser_item.add_argument("--file", action="append", default=[])
        parser_item.add_argument("--force", action="store_true")
        parser_item.set_defaults(func=cmd_github_gists_clone)
    for parser_item in (
        gh_snippets_clone,
        gh_snippet_clone,
    ):
        parser_item.add_argument("snippet_id", help="snippet id from list output")
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--user", default="")
        parser_item.add_argument("--token", default="")
        parser_item.add_argument("--input", default="data/snippets.json")
        parser_item.add_argument("--output-dir", default=".")
        parser_item.add_argument("--file", action="append", default=[])
        parser_item.add_argument("--force", action="store_true")
        parser_item.set_defaults(func=cmd_forge_snippets_clone)
    gh_gists_update = gh_gists_sub.add_parser("update", help="update a gist")
    gh_gist_update = gh_gist_sub.add_parser("update", help="update a gist")
    gh_snippets_update = gh_snippets_sub.add_parser("update", help="update a snippet")
    gh_snippet_update = gh_snippet_sub.add_parser("update", help="update a snippet")
    for parser_item in (
        gh_gists_update,
        gh_gist_update,
        gh_snippets_update,
        gh_snippet_update,
    ):
        parser_item.add_argument("gist_id")
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--file", action="append", default=[])
        parser_item.add_argument("--delete", action="append", default=[])
        parser_item.add_argument("--description", default=None)
        parser_item.add_argument("--token", default="")
        parser_item.add_argument("--force", "-f", action="store_true")
        parser_item.set_defaults(func=cmd_github_gists_update)

    gh_gists_create = gh_gists_sub.add_parser("create", help="create a gist")
    gh_gist_create = gh_gist_sub.add_parser("create", help="create a gist")
    gh_snippets_create = gh_snippets_sub.add_parser("create", help="create a snippet")
    gh_snippet_create = gh_snippet_sub.add_parser("create", help="create a snippet")
    for parser_item in (
        gh_gists_create,
        gh_gist_create,
        gh_snippets_create,
        gh_snippet_create,
    ):
        parser_item.add_argument("--server", default="")
        parser_item.add_argument("--file", action="append", default=[])
        parser_item.add_argument("--description", default=None)
        vis = parser_item.add_mutually_exclusive_group()
        vis.add_argument("--public", action="store_true")
        vis.add_argument("--private", action="store_true")
        parser_item.add_argument("--token", default="")
        parser_item.set_defaults(func=cmd_github_gists_create)


    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    if argcomplete:
        argcomplete.autocomplete(parser)
    args = parser.parse_args()

    # Handle global --tui mode only when no subcommand is requested.
    # Some subcommands also use a --tui flag for their own interactive flows.
    if args.tui and not args.command:
        raise SystemExit(cmd_tui(args))

    # If no command specified, show help
    if not args.command:
        parser.print_help()
        raise SystemExit(0)

    raise SystemExit(args.func(args))
