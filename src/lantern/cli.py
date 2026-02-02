import argparse
import json
import os
import subprocess
import sys
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

    if (
        up_ahead is not None
        and up_behind is not None
        and main_ahead is not None
        and main_behind is not None
        and up_ahead == main_ahead
        and up_behind == main_behind
    ):
        main_value = "≡"

    record["up"] = up_value
    record["main"] = main_value
    return record


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


def cmd_config_export(args: argparse.Namespace) -> int:
    config = lantern_config.load_config()
    payload = {
        "default_server": config.get("default_server", ""),
        "servers": _normalize_servers(config.get("servers", {})),
    }
    output_path = args.output or "git-lantern-servers.json"
    if output_path == "-":
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
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
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2)
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


def cmd_github_list(args: argparse.Namespace) -> int:
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
    output_path = args.output or "data/github.json"
    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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
    output_path = args.output or "data/gists.json"
    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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
        content = github.download_gist_file(raw_url, token)
        dest = os.path.join(output_dir, name)
        if os.path.exists(dest) and not args.force:
            print(f"File exists: {dest} (use --force to overwrite)", file=sys.stderr)
            return 1
        with open(dest, "wb") as handle:
            handle.write(content)
        print(f"Wrote {dest}")
    return 0


def cmd_forge_snippets_list(args: argparse.Namespace) -> int:
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
    output_path = args.output or "data/snippets.json"
    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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
            content = github.download_gist_file(raw_url, token)
            dest = os.path.join(output_dir, name)
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
            headers = forge._auth_headers("gitlab", user, token, auth)
            content = forge.download_with_headers(raw_url, headers)
            dest = os.path.join(output_dir, name)
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
        headers = forge._auth_headers("bitbucket", user, token, auth)
        for name in names:
            raw_url = (
                f"{base_api}/snippets/{urllib.parse.quote(user)}"
                f"/{urllib.parse.quote(str(snippet_id))}/files/{urllib.parse.quote(name)}"
            )
            content = forge.download_with_headers(raw_url, headers)
            dest = os.path.join(output_dir, name)
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
    sub = parser.add_subparsers(dest="command", required=True)

    servers = sub.add_parser("servers", help="list configured git servers")
    servers.set_defaults(func=cmd_servers)

    config = sub.add_parser("config", help="import/export server configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    config_export = config_sub.add_parser("export", help="export server config to JSON")
    config_export.add_argument("--output", default="git-lantern-servers.json")
    config_export.set_defaults(func=cmd_config_export)

    config_import = config_sub.add_parser("import", help="import server config from JSON")
    config_import.add_argument("--input", default="git-lantern-servers.json")
    config_import.add_argument("--output", default="")
    config_import.add_argument("--replace", action="store_true")
    config_import.set_defaults(func=cmd_config_import)

    config_path = config_sub.add_parser("path", help="print active config path")
    config_path.set_defaults(func=cmd_config_path)

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

    gh_list = forge_sub.add_parser("list", help="list repos to JSON")
    gh_list.add_argument("--server", default="")
    gh_list.add_argument("--user", default="")
    gh_list.add_argument("--token", default="")
    gh_list.add_argument("--include-forks", action="store_true")
    gh_list.add_argument("--output", default=None)
    gh_list.set_defaults(func=cmd_github_list)

    gh_clone = forge_sub.add_parser("clone", help="clone missing repos from JSON list")
    gh_clone.add_argument("--server", default="")
    gh_clone.add_argument("--input", default="data/github.json")
    gh_clone.add_argument("--root", default=os.getcwd())
    gh_clone.add_argument("--dry-run", action="store_true")
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
    raise SystemExit(args.func(args))
