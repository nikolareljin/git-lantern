import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Dict, List, MutableMapping, Optional, Tuple

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
    return matches


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


def cmd_tui(args: argparse.Namespace) -> int:
    """Main TUI interface for lantern."""
    if not _dialog_available():
        print("dialog is required for --tui mode.", file=sys.stderr)
        print("Install it with: apt install dialog (Debian/Ubuntu) or brew install dialog (macOS)", file=sys.stderr)
        return 1

    height, width = _dialog_init()

    # Session-level settings (persist throughout the TUI session)
    session = {
        "root": os.getcwd(),
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
            f"Hidden: {hidden_flag}  |  Forks: {forks_flag}"
        )
        menu_items: List[Tuple[str, str]] = [
            ("servers", "List configured Git servers"),
            ("config", "Server configuration"),
            ("settings", "Session settings"),
            ("repos", "List local repositories"),
            ("status", "Show repository status"),
            ("scan", "Scan repositories to JSON"),
            ("table", "Render table from JSON scan"),
            ("sync", "Sync repositories (fetch/pull/push)"),
            ("find", "Find repositories by name/remote"),
            ("duplicates", "Find duplicate repositories"),
            ("forge", "Git forge operations (list/clone)"),
            ("report", "Export report (CSV/JSON/MD)"),
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
                ("root", f"Change root directory (current: {session['root']})"),
                ("depth", f"Max scan depth (current: {session['max_depth']})"),
                ("hidden", f"Include hidden directories ({hidden_label})"),
                ("forks", f"Include forks in forge list ({forks_label})"),
                ("back", "Back to main menu"),
            ]
            settings_action = _dialog_menu("Session Settings", "Configure session settings:", settings_items, height, width)

            if settings_action == "root":
                new_root = _dialog_inputbox("Root Directory", "Enter the root directory for repository operations:", session["root"])
                if new_root:
                    if os.path.isdir(new_root):
                        session["root"] = os.path.abspath(new_root)
                        _dialog_msgbox("Settings", f"Root directory set to:\n{session['root']}")
                    else:
                        _dialog_msgbox("Error", f"Directory not found: {new_root}")
            elif settings_action == "depth":
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
                ("setup", "Interactive server setup"),
                ("path", "Show config file path"),
                ("export", "Export config to JSON"),
                ("import", "Import config from JSON"),
                ("back", "Back to main menu"),
            ]
            config_action = _dialog_menu("Configuration", "Select an operation:", config_items, height, width)

            if config_action == "setup":
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
            records = []
            for path in repos_list:
                record = build_repo_record(path, fetch)
                records.append(add_divergence_fields(record))
            columns = ["name", "branch", "upstream", "up", "main_ref", "main"]
            output_text = render_table(records, columns)
            _dialog_textbox_from_text("Status", output_text, height, width)

        elif action == "scan":
            if not _validate_session_root(session["root"], height, width):
                continue
            output = _dialog_inputbox("Output File", "Enter output JSON file path:", "data/repos.json")
            if output:
                fetch = _dialog_yesno("Fetch", "Run 'git fetch' before scanning?")
                repos_list = find_repos(session["root"], session["max_depth"], session["include_hidden"])
                records = [build_repo_record(path, fetch) for path in repos_list]
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
            input_file = _dialog_inputbox("Input File", "Enter JSON scan file path:", "data/repos.json")
            if not input_file:
                continue
            if not os.path.isfile(input_file):
                _dialog_msgbox("Error", f"File not found: {input_file}")
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
                input_file = _dialog_inputbox("Input File", "Enter JSON file with repository list:", "data/github.json")
                if input_file and os.path.isfile(input_file):
                    clone_root = _dialog_inputbox("Clone Directory", "Enter directory to clone into:", session["root"])
                    if clone_root:
                        # Use the existing TUI clone with dialog checklist
                        cmd_args = [sys.executable, "-m", "lantern", "forge", "clone", "--input", input_file, "--root", clone_root, "--tui"]
                        _run_lantern_subprocess(cmd_args, height, width, capture=False)
                elif input_file:
                    _dialog_msgbox("Error", f"File not found: {input_file}")

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
            input_file = _dialog_inputbox("Input File", "Enter JSON scan file path:", "data/repos.json")
            if not input_file:
                continue
            if not os.path.isfile(input_file):
                _dialog_msgbox("Error", f"File not found: {input_file}")
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
    columns = ["name", "path", "origin"]
    print(render_table(records, columns))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    repos = find_repos(args.root, args.max_depth, args.include_hidden)
    records = [build_repo_record(path, args.fetch) for path in repos]
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
    records = []
    for path in repos:
        record = build_repo_record(path, args.fetch)
        records.append(add_divergence_fields(record))
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

    # Handle --tui flag: launch interactive TUI mode
    if args.tui:
        raise SystemExit(cmd_tui(args))

    # If no command specified, show help
    if not args.command:
        parser.print_help()
        raise SystemExit(0)

    raise SystemExit(args.func(args))
