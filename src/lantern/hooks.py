"""lantern hooks: the script-helpers per-repository hooks across the owner's clones.

A clone is the owner's when its origin is on github.com under one of the owners
in `git config --global script-helpers.owners` (comma or space separated, any
number of namespaces). Every other clone (an employer's, a fork of someone
else's) is listed and never changed.

The work is done by script-helpers' setup-hooks.sh (0.50.0 or newer), found at
`git config --global script-helpers.home`, else in lantern's own vendored
copy. lantern only decides which clones and which options.
"""
import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

MIN_VERSION = (0, 50, 0)
AGENTS = ("codex", "gemini", "copilot", "cursor")
EXPORT_VERSION = 1


class HooksError(Exception):
    """A configuration problem: no owners, no usable setup-hooks.sh (exit 2)."""


def _git(args: List[str], cwd: Optional[str] = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return result.stdout.strip()


def configured_owners() -> List[str]:
    raw = os.environ.get("SCRIPT_HELPERS_OWNERS") or _git(["config", "--global", "--get", "script-helpers.owners"])
    return [o.lower() for o in re.split(r"[,\s]+", raw) if o]


_GITHUB = re.compile(r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[^@/]+@)?github\.com(?::\d+)?[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)


def github_slug(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """(owner, repo) in lower case for a github.com URL, else None."""
    m = _GITHUB.match((url or "").strip())
    return (m.group(1).lower(), m.group(2).lower()) if m else None


def origin_url(path: str) -> str:
    return _git(["-C", path, "remote", "get-url", "origin"])


def is_owned(path: str, owners: List[str]) -> bool:
    slug = github_slug(origin_url(path))
    return bool(slug) and slug[0] in owners


def _version_tuple(text: str) -> Tuple[int, ...]:
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_setup_script() -> str:
    """The setup-hooks.sh to run. HooksError when none is usable."""
    candidates = []
    home = os.environ.get("SCRIPT_HELPERS_HOME") or _git(["config", "--global", "--get", "script-helpers.home"])
    if home:
        candidates.append(os.path.expanduser(home))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.normpath(os.path.join(here, "..", "..", "scripts", "script-helpers")))
    tried = []
    for root in candidates:
        script = os.path.join(root, "scripts", "setup-hooks.sh")
        if not os.path.isfile(script):
            tried.append("{} (no scripts/setup-hooks.sh)".format(root))
            continue
        try:
            with open(os.path.join(root, "VERSION")) as fh:
                version = fh.read().strip()
        except OSError:
            version = ""
        if _version_tuple(version) < MIN_VERSION:
            tried.append("{} (version {}, need 0.50.0 or newer)".format(root, version or "unknown"))
            continue
        return script
    raise HooksError("no usable script-helpers: " + "; ".join(tried) +
                     ". Set one: git config --global script-helpers.home <script-helpers checkout>")


def run_setup(script: str, path: str, args: List[str]) -> Tuple[int, str]:
    result = subprocess.run(["bash", script, *args], cwd=path, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return result.returncode, result.stdout


def repo_state(script: str, path: str) -> Dict[str, str]:
    """setup-hooks.sh --status --porcelain as a dict; {} when it fails."""
    rc, out = run_setup(script, path, ["--status", "--porcelain"])
    if rc != 0:
        return {}
    line = out.strip().splitlines()[-1] if out.strip() else ""
    state = {}
    for field in line.split("\t"):
        if "=" in field:
            key, value = field.split("=", 1)
            state[key] = value
    return state


def agent_list(spec: str) -> List[str]:
    """--agents value: claude, codex, gemini, copilot, cursor, comma-separated, or all."""
    if not spec:
        return []
    names = ["claude", *AGENTS] if spec.strip() == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    bad = [n for n in names if n not in ("claude", *AGENTS)]
    if bad:
        raise HooksError("unknown agent(s): {} (claude, codex, gemini, copilot, cursor or all)".format(", ".join(bad)))
    return names


def agent_commands(agents: List[str], uninstall: bool = False) -> List[List[str]]:
    """setup-hooks.sh argument lists for the agents: --claude-repo, then --agent-repo."""
    tail = ["--uninstall"] if uninstall else []
    cmds = []
    if "claude" in agents:
        cmds.append(["--claude-repo", *tail])
    others = [a for a in agents if a != "claude"]
    if others:
        cmds.append(["--agent-repo", ",".join(others), *tail])
    return cmds


def present_agents(state: Dict[str, str]) -> List[str]:
    found = ["claude"] if state.get("claude_repo") == "yes" else []
    found += [a for a in (state.get("agents") or "").split(",") if a]
    return found


def plan_apply(state: Dict[str, str], agents: List[str]) -> List[List[str]]:
    """What apply runs in one owned clone, given its state; [] when nothing is needed."""
    steps = []
    mode = state.get("mode", "none")
    if mode == "none":
        steps.append([])                      # the per-repository setup itself
    elif state.get("taken_over") == "yes":
        steps.append(["--repair"])
    have = set(present_agents(state))
    missing = [a for a in agents if a not in have]
    steps.extend(agent_commands(missing))
    return steps


def describe(step: List[str]) -> str:
    return "setup-hooks.sh " + " ".join(step) if step else "setup-hooks.sh"


def normalize_remote(url: str) -> str:
    """A remote URL in one form for matching across machines: owner/repo on github.com, else the URL."""
    slug = github_slug(url)
    if slug:
        return "github.com/{}/{}".format(*slug)
    return re.sub(r"\.git/?$", "", (url or "").strip().lower())


def export_entries(script: str, repos: List[str], owners: List[str]) -> Dict[str, object]:
    entries = []
    for path in repos:
        if not is_owned(path, owners):
            continue
        state = repo_state(script, path)
        agents = present_agents(state)
        if state.get("mode", "none") == "none" and not agents:
            continue
        entries.append({"remote": normalize_remote(origin_url(path)),
                        "git_hooks": state.get("mode", "none") != "none",
                        "agents": agents})
    return {"version": EXPORT_VERSION, "kind": "lantern-hooks", "owners": owners, "repos": entries}


def load_export(path: str) -> Dict[str, object]:
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("kind") != "lantern-hooks" or not isinstance(data.get("repos"), list):
        raise HooksError("{} is not a lantern hooks export".format(path))
    if data.get("version") != EXPORT_VERSION:
        raise HooksError("{}: export version {} is not supported (this lantern reads {})".format(
            path, data.get("version"), EXPORT_VERSION))
    return data


# ---------------------------------------------------------------- commands
# Each takes the clones found under --root and the parsed arguments, prints,
# and returns the exit code: 0 done, 1 a clone failed, 2 configuration.

def _setup(args) -> Tuple[str, List[str]]:
    owners = configured_owners()
    if not owners:
        raise HooksError("no owners configured: git config --global script-helpers.owners <user-or-org>[,<another>]")
    return find_setup_script(), owners


def _selected(repos: List[str], only: str) -> List[str]:
    names = {n.strip().lower() for n in (only or "").split(",") if n.strip()}
    return [r for r in repos if not names or os.path.basename(r).lower() in names]


def cmd_status(repos: List[str], args) -> int:
    script, owners = _setup(args)
    print("owners: {}   script-helpers: {}".format(",".join(owners), os.path.dirname(os.path.dirname(script))))
    for path in _selected(repos, getattr(args, "only", "")):
        if not is_owned(path, owners):
            print("{:<40} not owned, left alone".format(os.path.basename(path)))
            continue
        st = repo_state(script, path)
        if not st:
            print("{:<40} status failed".format(os.path.basename(path)))
            continue
        flag = "  TAKEN OVER (lantern hooks repair)" if st.get("taken_over") == "yes" else ""
        print("{:<40} git hooks: {:<8} agents: {}{}".format(
            os.path.basename(path), st.get("mode", "?"), ",".join(present_agents(st)) or "-", flag))
    return 0


def cmd_apply(repos: List[str], args) -> int:
    script, owners = _setup(args)
    agents = agent_list(getattr(args, "agents", ""))
    dry = bool(getattr(args, "dry_run", False))
    batch = int(getattr(args, "batch", 0) or 0)
    failed = done = 0
    for path in _selected(repos, getattr(args, "only", "")):
        name = os.path.basename(path)
        if not is_owned(path, owners):
            print("{:<40} not owned, left alone".format(name))
            continue
        steps = plan_apply(repo_state(script, path), agents)
        if not steps:
            print("{:<40} up to date".format(name))
            continue
        if batch and done >= batch:
            print("{:<40} next batch: {}".format(name, "; ".join(describe(s) for s in steps)))
            continue
        done += 1
        for step in steps:
            if dry:
                print("{:<40} would run: {}".format(name, describe(step)))
                continue
            rc, out = run_setup(script, path, step)
            print("{:<40} {}: {}".format(name, describe(step), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
            if rc != 0:
                failed += 1
                print("    " + out.strip().replace("\n", "\n    "))
                break
    return 1 if failed else 0


def cmd_repair(repos: List[str], args, quiet: bool = False) -> int:
    script, owners = _setup(args)
    failed = 0
    for path in _selected(repos, getattr(args, "only", "")):
        if not is_owned(path, owners) or repo_state(script, path).get("taken_over") != "yes":
            continue
        rc, out = run_setup(script, path, ["--repair"])
        print("{:<40} repair: {}".format(os.path.basename(path), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
        if rc != 0:
            failed += 1
            print("    " + out.strip().replace("\n", "\n    "))
    if not quiet and not failed:
        print("nothing else to repair")
    return 1 if failed else 0


def cmd_remove(repos: List[str], args) -> int:
    script, owners = _setup(args)
    agents = agent_list(getattr(args, "agents", ""))
    failed = 0
    for path in _selected(repos, getattr(args, "only", "")):
        if not is_owned(path, owners):
            continue
        st = repo_state(script, path)
        steps = agent_commands([a for a in agents if a in present_agents(st)], uninstall=True)
        if not agents and st.get("mode", "none") != "none":
            steps.append(["--uninstall"])
        for step in steps:
            rc, out = run_setup(script, path, step)
            print("{:<40} {}: {}".format(os.path.basename(path), describe(step), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
            if rc != 0:
                failed += 1
                print("    " + out.strip().replace("\n", "\n    "))
    return 1 if failed else 0


def cmd_export(repos: List[str], args) -> int:
    script, owners = _setup(args)
    data = export_entries(script, repos, owners)
    text = json.dumps(data, indent=2) + "\n"
    if args.file == "-":
        print(text, end="")
    else:
        with open(args.file, "w") as fh:
            fh.write(text)
        print("exported {} repositories to {}".format(len(data["repos"]), args.file))
    return 0


def cmd_import(repos: List[str], args) -> int:
    script, owners = _setup(args)
    data = load_export(args.file)
    local = {}
    for path in repos:
        if is_owned(path, owners):
            local.setdefault(normalize_remote(origin_url(path)), path)
    failed = 0
    unmatched = []
    for entry in data["repos"]:
        if not isinstance(entry, dict):
            continue
        path = local.get(str(entry.get("remote") or ""))
        if not path:
            unmatched.append(str(entry.get("remote")))
            continue
        agents = [a for a in entry.get("agents") or [] if a in ("claude", *AGENTS)]
        st = repo_state(script, path)
        steps = plan_apply(st, agents) if entry.get("git_hooks") else agent_commands(
            [a for a in agents if a not in present_agents(st)])
        for step in steps:
            if getattr(args, "dry_run", False):
                print("{:<40} would run: {}".format(os.path.basename(path), describe(step)))
                continue
            rc, out = run_setup(script, path, step)
            print("{:<40} {}: {}".format(os.path.basename(path), describe(step), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
            if rc != 0:
                failed += 1
                print("    " + out.strip().replace("\n", "\n    "))
                break
    for remote in unmatched:
        print("not cloned here (or not under an owner): {}".format(remote))
    return 1 if failed else 0
