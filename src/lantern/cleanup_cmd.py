"""CLI and dialog frontends for the shared branch cleanup policy."""

import argparse
from collections import Counter
import contextlib
import io
import os
import subprocess
import sys
import tempfile

from . import cleanup, cleanup_forge, config, progress


def add_parser(sub):
    parser = sub.add_parser("cleanup", help="clean old merged branches in selected repositories")
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--server", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--include-forks", action="store_true")
    parser.add_argument("--org", dest="orgs", action="append", default=[])
    parser.add_argument("--all-orgs", action="store_true")
    parser.add_argument("--with-user", action="store_true")
    parser.add_argument("--repos", default="", help="comma-separated full repository names or clone paths")
    parser.add_argument("--select", action="store_true", help="select repositories using checkboxes (requires dialog)")
    parser.add_argument("--older-than", choices=cleanup.AGES, default="30d")
    parser.add_argument("--confirm-first", action="store_true", help="review branches per repository; unmerged and unknown-age branches start unchecked")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="delete eligible branches; otherwise preview only")
    action.add_argument("--dry-run", action="store_true", help="preview without deleting branches")
    parser.set_defaults(func=command, input="")


def repositories(args):
    from . import cli

    provider, base, user, token, auth, server = cli._fleet_server_context(args)
    progress.report("Listing repositories in the selected server/group...")
    remote = cli._fleet_load_remote(args)
    allowed = {repo["name"] for repo in remote["repos"]}
    rows = []
    found = set()
    progress.report("Scanning the workspace for local clones...")
    paths = cli.find_repos(args.root, args.max_depth, args.include_hidden)
    for index, path in enumerate(paths, 1):
        progress.report(f"Matching local clone {index}/{len(paths)}: {os.path.basename(path)}")
        origin = cleanup.git(path, "remote", "get-url", "origin", check=False)
        if origin.returncode:
            continue
        _, name = cleanup_forge.repo_url(origin.stdout.strip())
        if name not in allowed:
            continue
        adapter = cleanup_forge.Provider(provider, base, user, token, auth, name)
        try:
            adapter.verify_origin(origin.stdout.strip())
        except cleanup.Unsafe:
            continue
        # Organization tokens used to discover a repository must also authorize its checks.
        orgs, _, _ = cli._resolve_org_selection(args, server)
        for org in orgs:
            if name.startswith(org["name"] + "/") and org.get("token"):
                adapter = cleanup_forge.Provider(provider, base, user, org["token"], auth, name)
                break
        rows.append({"name": name, "path": path, "adapter": adapter})
        found.add(name)
    for name in sorted(allowed - found):
        rows.append({"name": name, "path": "", "adapter": None})
    return rows


def choose(title, text, rows, details=""):
    from . import cli

    if not details:
        return cli._dialog_checklist(title, text, rows)
    with tempfile.TemporaryDirectory(prefix="lantern-branch-details-") as directory:
        filename = os.path.join(directory, "details.txt")
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write(details)
        return cli._dialog_checklist_result(
            title, text, rows, help_file=filename, ok_label="Review deletion",
        ) or []


def run(args):
    from . import cli

    interactive = args.select or not args.repos or args.confirm_first
    if interactive and (not sys.stdin.isatty() or not cli._dialog_available()):
        raise cleanup.Unsafe("interactive selection requires a terminal and dialog; use --repos without --confirm-first for automation")
    def work(text, operation, *arguments):
        if interactive:
            return cli._dialog_work("Branch cleanup", text, operation, *arguments)
        return operation(*arguments)

    records = work("Preparing repository selection...", repositories, args)
    if args.repos:
        requested = args.repos.split(",")
        selected = []
        for key in requested:
            matches = [r for r in records if key.strip() in (r["name"], r["path"])]
            if len(matches) != 1:
                raise cleanup.Unsafe("repository missing or ambiguous in selected scope: " + key)
            if matches[0] not in selected:
                selected.append(matches[0])
        records = selected
    if not records:
        print("No repositories found in the selected workspace/server/group. Nothing deleted.")
        return 0
    if args.select or not args.repos:
        tags = choose("Branch cleanup", "Select repositories. Only old eligible branches will be considered.",
                      [(str(i), r["name"] + " " + (r["path"] or "[not cloned]"), False)
                       for i, r in enumerate(records)])
        records = [r for i, r in enumerate(records) if str(i) in tags]
    if not records:
        print("No repositories selected; nothing deleted.")
        return 0
    failures = 0
    inspected = deleted = eligible_total = 0
    with cleanup.Observations() as observations:
        for index, record in enumerate(records, 1):
            path, adapter, name = record["path"], record["adapter"], record["name"]
            heading = f"Repository {index}/{len(records)}: {name}"
            print(heading + ":")
            if not path:
                print("  skipped: repository is not cloned")
                continue
            try:
                rows = work(heading + "\nInspecting local and remote branches...",
                            cleanup.scan, path, adapter, observations, args.older_than, None, args.confirm_first)
                inspected += len(rows)
                for row in rows:
                    verdict = row.reason or ("eligible" if row.merged else "requires explicit unmerged selection")
                    print("  " + row.label())
                    print("    " + verdict)
                eligible = [r for r in rows if not r.reason and (r.merged or args.confirm_first)]
                eligible_total += len(eligible)
                print(f"  Inspected {len(rows)} branches; {len(eligible)} eligible for this mode.")
                if not eligible:
                    print("  No eligible branches. Nothing deleted in this repository.")
                    if any("first observation" in row.reason for row in rows):
                        print(f"  Branch age is not yet established. First-seen observations must be older than {args.older_than}.")
                if args.confirm_first and not eligible:
                    reasons = Counter(row.reason or "not eligible in this mode" for row in rows)
                    notice = f"{name}: 0 selectable branches ({len(rows)} inspected).\n\n"
                    notice += "\n".join(f"{count}: {reason}" for reason, count in reasons.most_common(5))
                    if any("first observation" in row.reason for row in rows):
                        notice += (f"\n\nBranches must be observed unchanged for {args.older_than}. "
                                   "An old commit does not establish branch creation age.")
                    notice += "\n\nNothing deleted. The final branch list is a read-only report with all skip reasons."
                    cli._dialog_msgbox("No branches eligible for selection", notice, 0, 0)
                if args.confirm_first and eligible:
                    details = "\n\n".join("[{}] {}".format(i, row.label().replace("; ", "\n    "))
                                            for i, row in enumerate(eligible))
                    tags = choose("Branch cleanup: " + name,
                                  "Space: toggle branch. F1: full branch details. Unknown creation age starts unchecked.\n"
                                  "UNMERGED branches can contain work absent from the default branch.",
                                  [(str(i), row.label(), row.merged and not row.age_uncertain) for i, row in enumerate(eligible)], details=details)
                    eligible = [row for i, row in enumerate(eligible) if str(i) in tags]
                    if not eligible:
                        print("  No branches selected; nothing deleted in this repository.")
                        continue
                    uncertain = [row for row in eligible if row.age_uncertain]
                    if uncertain and args.apply and not cli._dialog_yesno(
                        "Approve unknown branch creation age",
                        f"Creation dates cannot be established for {len(uncertain)} selected branches in {name}.\n\n"
                        "Their activity is old, but they may be newly created refs pointing to old commits. "
                        "Allow deletion of these specific selected branches despite unknown creation age?",
                        height=0, width=0, default_no=True,
                    ):
                        print("  cancelled: unknown creation age was not approved; nothing deleted in this repository")
                        continue
                    if eligible and args.apply and not cli._dialog_yesno(
                        "Confirm branch deletion", "Delete {} selected branches in {}?".format(len(eligible), name),
                        default_no=True,
                    ):
                        print("  cancelled: nothing deleted in this repository")
                        continue
                if not args.apply:
                    for row in eligible:
                        print("  dry-run: " + row.label())
                    continue
                for row in eligible:
                    # Rebuild evidence after the prompt and before each individual deletion.
                    fresh = work(heading + f"\nRechecking {row.scope} {row.name} before deletion...",
                                 cleanup.scan, path, adapter, observations, args.older_than, None, args.confirm_first)
                    current = next((r for r in fresh if (r.name, r.scope) == (row.name, row.scope)), None)
                    if current is None or current.sha != row.sha or current.reason or current.merged != row.merged or current.age_uncertain != row.age_uncertain:
                        print("  skipped {} {}: evidence changed or branch disappeared".format(row.scope, row.name))
                        continue
                    work(heading + f"\nDeleting {row.scope} {row.name}...", cleanup.delete, path, adapter, current)
                    deleted += 1
                    print("  deleted {} {}".format(row.scope, row.name))
            except (cleanup.Unsafe, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
                failures += 1
                # Provider exceptions use fixed messages; raw transport errors may contain credentials.
                detail = str(exc) if isinstance(exc, cleanup.Unsafe) else "cleanup evidence or operation failed"
                print("  failed: " + detail, file=sys.stderr)
    print(f"\nCleanup summary: {len(records)} repositories selected; {inspected} branches inspected; "
          f"{eligible_total} eligible; {deleted} deleted; {failures} repository failures.")
    if not eligible_total:
        print("No eligible branches: no branch checkbox list was opened. See the skip reasons above; nothing deleted.")
    return 1 if failures else 0


def command(args):
    try:
        return run(args)
    except (cleanup.Unsafe, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        detail = str(exc) if isinstance(exc, cleanup.Unsafe) else "cleanup evidence unavailable"
        print(detail, file=sys.stderr)
        return 1


def tui(session, height, width):
    from . import cli

    settings = config.load_config()
    server_name = cli._tui_select_server(settings, height, width)
    if server_name is None:
        return
    server = config.get_server(settings, server_name)
    scopes = [("user", "Personal repositories")]
    scopes += [("org:" + entry["name"], entry["name"]) for entry in config.get_server_organizations(server)]
    scope = cli._dialog_menu("Cleanup scope", "Choose repository group:", scopes, height, width)
    if not scope:
        return
    age = cli._dialog_menu("Branch age", "Minimum inactivity (automatic cleanup also requires observed age):",
                           [("30d", "30 days (default)"), ("60d", "60 days"), ("90d", "90 days"),
                            ("6m", "6 months"), ("1y", "1 year")], height, width)
    if not age:
        return
    options = cli._dialog_checklist_result(
        "Cleanup options", "Confirm first is optional. Continue to choose repositories.",
        [("confirm", "Confirm first: review branch checkboxes for each repository", False)], height, width,
    )
    if options is None:
        return
    args = argparse.Namespace(
        root=session["root"], max_depth=session["max_depth"], include_hidden=session["include_hidden"],
        server=server_name, user="", token="", include_forks=session["include_forks"],
        orgs=[scope[4:]] if scope.startswith("org:") else [], all_orgs=False, with_user=False,
        repos="", select=True, older_than=age, confirm_first="confirm" in options,
        apply=True, dry_run=False, input="",
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        status = command(args)
    cli._dialog_textbox_from_text("Cleanup results (read-only)" + (" (errors)" if status else ""), output.getvalue(), height, width)
