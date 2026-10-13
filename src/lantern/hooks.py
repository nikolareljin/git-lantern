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


def _windows_git() -> Optional[str]:
    """git.exe from an absolute PATH entry. Windows looks for a bare "git" in
    the current directory first, so an untrusted clone could plant one."""
    return next((os.path.join(d, "git.exe") for d in os.environ.get("PATH", "").split(os.pathsep)
                 if os.path.isabs(d) and os.path.isfile(os.path.join(d, "git.exe"))), None)


def _git(args: List[str], cwd: Optional[str] = None) -> str:
    exe = (_windows_git() or "git.exe-not-on-PATH") if os.name == "nt" else "git"
    try:
        result = subprocess.run([exe, *args], cwd=cwd, check=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return result.stdout.strip()


def configured_owners() -> List[str]:
    raw = os.environ.get("SCRIPT_HELPERS_OWNERS") or _git(["config", "--global", "--get", "script-helpers.owners"])
    return [o.lower() for o in re.split(r"[,\s]+", raw) if o]


_GITHUB = re.compile(r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[^@/]+@)?(?:www\.|ssh\.)?github\.com(?::\d+)?[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)


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
    # A configured home is used or refused, never skipped: falling back to the
    # vendored copy would hide a mistyped path behind an older script.
    home = os.environ.get("SCRIPT_HELPERS_HOME") or _git(["config", "--global", "--get", "script-helpers.home"])
    if home:
        candidates = [os.path.expanduser(home)]
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [os.path.normpath(os.path.join(here, "..", "..", "scripts", "script-helpers"))]
    tried = []
    for root in candidates:
        script = os.path.join(root, "scripts", "setup-hooks.sh")
        if not os.path.isfile(script):
            tried.append("{} (no scripts/setup-hooks.sh)".format(root))
            continue
        try:
            with open(os.path.join(root, "VERSION"), encoding="utf-8") as fh:
                version = fh.read().strip()
        except OSError:
            version = ""
        if _version_tuple(version) < MIN_VERSION:
            tried.append("{} (version {}, need 0.50.0 or newer)".format(root, version or "unknown"))
            continue
        return script
    raise HooksError("no usable script-helpers: " + "; ".join(tried) +
                     ". Set one: git config --global script-helpers.home <script-helpers checkout>")


_BASH: Optional[str] = None


def find_bash() -> str:
    """The bash that runs setup-hooks.sh. On Windows that is Git for Windows'
    bash: a bare "bash" there resolves to System32's WSL launcher first, which
    cannot run a script given by its Windows path. The lookup order is
    script-helpers' scripts/setup-hooks.ps1 (Find-GitBash); keep the two equal."""
    global _BASH
    if _BASH:
        return _BASH
    if os.name != "nt":
        _BASH = "bash"
        return _BASH
    candidates = []
    if os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        candidates.append(os.environ["CLAUDE_CODE_GIT_BASH_PATH"])
    git = _windows_git()       # absolute PATH entries only; see _windows_git
    if git:
        d = os.path.dirname(git)
        for up in (d, os.path.dirname(d), os.path.dirname(os.path.dirname(d))):
            candidates.append(os.path.join(up, "bin", "bash.exe"))
        exec_path = _git(["--exec-path"])      # <Git>/mingw64/libexec/git-core
        if exec_path:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.normpath(exec_path))))
            candidates.append(os.path.join(root, "bin", "bash.exe"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
            candidates.append(os.path.join(base, "Programs", "Git", "bin", "bash.exe"))
    for c in candidates:
        if os.path.isfile(c):
            _BASH = c
            return c
    raise HooksError("Git Bash not found; install Git for Windows, or set CLAUDE_CODE_GIT_BASH_PATH "
                     "to its bin\\bash.exe")


def run_setup(script: str, path: str, args: List[str]) -> Tuple[int, str]:
    # stdin closed: setup-hooks.sh must never wait on the terminal for an answer.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run([find_bash(), script.replace(os.sep, "/"), *args], cwd=path, check=False,
                                stdin=subprocess.DEVNULL, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HooksError("cannot run bash: {}".format(exc))
    return result.returncode, result.stdout


def repo_state(script: str, path: str) -> Optional[Dict[str, str]]:
    """setup-hooks.sh --status --porcelain as a dict; None when it fails, which
    callers treat as a failed clone (never as "nothing installed")."""
    rc, out = run_setup(script, path, ["--status", "--porcelain"])
    if rc != 0 or "mode=" not in out:
        return None
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
    elif needs_repair(state):
        steps.append(["--repair"])
    have = set(present_agents(state))
    missing = [a for a in agents if a not in have]
    steps.extend(agent_commands(missing))
    return steps


def needs_repair(state: Dict[str, str]) -> bool:
    """Another tool took core.hooksPath over, or the setup points straight at the
    repository's own hooks, so the current security checks do not run there
    (script-helpers reports that as unscanned=yes)."""
    return state.get("taken_over") == "yes" or state.get("unscanned") == "yes"


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
    by_remote: Dict[str, Dict[str, object]] = {}
    for path in repos:
        if not is_owned(path, owners):
            continue
        state = repo_state(script, path)
        if state is None:
            raise HooksError("status failed in {}; nothing exported".format(path))
        agents = present_agents(state)
        if state.get("mode", "none") == "none" and not agents:
            continue
        remote = normalize_remote(origin_url(path))
        if remote in by_remote:          # another clone of the same repository: one entry, the union
            e = by_remote[remote]
            e["git_hooks"] = e["git_hooks"] or state.get("mode", "none") != "none"
            e["agents"] += [a for a in agents if a not in e["agents"]]
            continue
        by_remote[remote] = {"remote": remote, "git_hooks": state.get("mode", "none") != "none", "agents": agents}
        entries.append(by_remote[remote])
    return {"version": EXPORT_VERSION, "kind": "lantern-hooks", "owners": owners, "repos": entries}


def load_export(path: str) -> Dict[str, object]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise HooksError("cannot read {}: {}".format(path, exc.strerror or exc))
    except ValueError as exc:
        raise HooksError("{} is not JSON: {}".format(path, exc))
    if not isinstance(data, dict) or data.get("kind") != "lantern-hooks" or not isinstance(data.get("repos"), list):
        raise HooksError("{} is not a lantern hooks export".format(path))
    version = data.get("version")
    if type(version) is not int or version != EXPORT_VERSION:
        raise HooksError("{}: export version {} is not supported (this lantern reads {})".format(
            path, version, EXPORT_VERSION))
    for i, entry in enumerate(data["repos"]):
        ok = (isinstance(entry, dict) and isinstance(entry.get("remote"), str) and entry["remote"].strip()
              and isinstance(entry.get("git_hooks"), bool) and isinstance(entry.get("agents"), list)
              and all(a in ("claude", *AGENTS) for a in entry["agents"]))
        if not ok:
            raise HooksError("{}: repos[{}] needs remote (text), git_hooks (true/false) and agents "
                             "(a list of claude,{})".format(path, i, ",".join(AGENTS)))
        entry["remote"] = normalize_remote(entry["remote"])
    return data


# ---------------------------------------------------------------- commands
# Each takes the clones found under --root and the parsed arguments, prints,
# and returns the exit code: 0 done, 1 a clone failed, 2 configuration.

def _setup(args) -> Tuple[str, List[str]]:
    owners = configured_owners()
    if not owners:
        raise HooksError("no owners configured: git config --global script-helpers.owners <user-or-org>[,<another>]")
    return find_setup_script(), owners


def linked_worktree_of(path: str) -> Optional[str]:
    """The main clone of a linked worktree, else None. Worktrees share the
    main clone's config, so changing one would change the other."""
    out = _git(["rev-parse", "--git-dir", "--git-common-dir"], cwd=path).splitlines()
    if len(out) != 2:
        return None
    git_dir, common = (os.path.normcase(os.path.realpath(os.path.join(path, d))) for d in out)   # both may be relative
    if git_dir == common:
        return None
    return os.path.dirname(os.path.realpath(os.path.join(path, out[1])))


def _resolve_only(repos: List[str], only: str, root: str) -> Optional[List[str]]:
    """The clones named by --only, or None when it is empty. Each item is a
    path (absolute, or relative to the current directory or --root) or a
    clone directory name; a name that fits two clones, or an item that fits
    none, is refused."""
    items = [n.strip() for n in (only or "").split(",") if n.strip()]
    if not items:
        return None
    real = {os.path.normcase(os.path.realpath(r)): r for r in repos}
    picked: List[str] = []
    for item in items:
        if os.path.isabs(item) or "/" in item or os.sep in item:
            tries = [os.path.normcase(os.path.realpath(t)) for t in
                     [item] + ([] if os.path.isabs(item) else [os.path.join(root, item)])]
            hits = [real[t] for t in tries if t in real][:1]
        else:
            hits = [r for r in repos if os.path.basename(r).lower() == item.lower()]
        if not hits:
            raise HooksError("--only {}: no clone under --root matches".format(item))
        if len(hits) > 1:
            raise HooksError("--only {} matches {} clones; give a path instead:\n  {}".format(
                item, len(hits), "\n  ".join(hits)))
        if hits[0] not in picked:
            picked.append(hits[0])
    return picked


def _label(path: str, args) -> str:
    """A clone as shown in output: its path under --root, so two clones with
    the same directory name stay apart."""
    root = getattr(args, "root", None) or os.getcwd()
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:                  # another drive on Windows
        return path
    return path if rel == ".." or rel.startswith(".." + os.sep) else rel


def _selected(repos: List[str], args, quiet: bool = False) -> List[str]:
    only = _resolve_only(repos, getattr(args, "only", ""), getattr(args, "root", None) or os.getcwd())
    picked = []
    for r in (repos if only is None else only):
        main = linked_worktree_of(r)
        if main:
            if not quiet:
                print("{:<40} worktree of {}, set up through it".format(_label(r, args), main))
            continue
        picked.append(r)
    return picked


def cmd_status(repos: List[str], args) -> int:
    script, owners = _setup(args)
    selected = _selected(repos, args)
    print("owners: {}   script-helpers: {}".format(",".join(owners), os.path.dirname(os.path.dirname(script))))
    failed = 0
    for path in selected:
        if not is_owned(path, owners):
            print("{:<40} not owned, left alone".format(_label(path, args)))
            continue
        st = repo_state(script, path)
        if st is None:
            print("{:<40} status failed".format(_label(path, args)))
            failed += 1
            continue
        flag = ("  TAKEN OVER (lantern hooks repair)" if st.get("taken_over") == "yes" else
                "  OWN HOOKS ONLY, NOT SCANNED (lantern hooks repair)" if st.get("unscanned") == "yes" else "")
        print("{:<40} git hooks: {:<8} agents: {}{}".format(
            _label(path, args), st.get("mode", "?"), ",".join(present_agents(st)) or "-", flag))
    return 1 if failed else 0


def cmd_apply(repos: List[str], args) -> int:
    script, owners = _setup(args)
    agents = agent_list(getattr(args, "agents", ""))
    dry = bool(getattr(args, "dry_run", False))
    batch = int(getattr(args, "batch", 0) or 0)
    failed = done = 0
    for path in _selected(repos, args):
        name = _label(path, args)
        if not is_owned(path, owners):
            print("{:<40} not owned, left alone".format(name))
            continue
        state = repo_state(script, path)
        if state is None:
            print("{:<40} status failed, skipped".format(name))
            failed += 1
            continue
        steps = plan_apply(state, agents)
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
    for path in _selected(repos, args):
        if not is_owned(path, owners):
            continue
        state = repo_state(script, path)
        if state is None:
            print("{:<40} status failed, skipped".format(_label(path, args)))
            failed += 1
            continue
        if not needs_repair(state):
            continue
        rc, out = run_setup(script, path, ["--repair"])
        print("{:<40} repair: {}".format(_label(path, args), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
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
    for path in _selected(repos, args):
        if not is_owned(path, owners):
            continue
        st = repo_state(script, path)
        if st is None:
            print("{:<40} status failed, skipped".format(_label(path, args)))
            failed += 1
            continue
        # No --agents: everything this setup added (agent hooks, then the git
        # hooks). With --agents: only those agents.
        wanted = agents or present_agents(st)
        steps = agent_commands([a for a in wanted if a in present_agents(st)], uninstall=True)
        if not agents and st.get("mode", "none") != "none":
            steps.append(["--uninstall"])
        for step in steps:
            rc, out = run_setup(script, path, step)
            print("{:<40} {}: {}".format(_label(path, args), describe(step), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
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
        try:
            with open(args.file, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            raise HooksError("cannot write {}: {}".format(args.file, exc.strerror or exc))
        print("exported {} repositories to {}".format(len(data["repos"]), args.file))
    return 0


def cmd_import(repos: List[str], args) -> int:
    script, owners = _setup(args)
    data = load_export(args.file)
    local: Dict[str, List[str]] = {}
    selected = set(_selected(repos, args, quiet=True))
    elsewhere = set()                   # cloned here, but left out by --only
    for path in repos:
        if is_owned(path, owners):
            remote = normalize_remote(origin_url(path))
            if path in selected:
                local.setdefault(remote, []).append(path)
            else:
                elsewhere.add(remote)
    failed = 0
    unmatched = []
    for entry in data["repos"]:
        paths = local.get(entry["remote"])
        if not paths:
            if entry["remote"] not in elsewhere:
                unmatched.append(entry["remote"])
            continue
        agents = list(entry["agents"])
        for path in paths:              # every clone of that repository here
            st = repo_state(script, path)
            if st is None:
                print("{:<40} status failed, skipped".format(_label(path, args)))
                failed += 1
                continue
            steps = plan_apply(st, agents) if entry.get("git_hooks") else agent_commands(
                [a for a in agents if a not in present_agents(st)])
            for step in steps:
                if getattr(args, "dry_run", False):
                    print("{:<40} would run: {}".format(_label(path, args), describe(step)))
                    continue
                rc, out = run_setup(script, path, step)
                print("{:<40} {}: {}".format(_label(path, args), describe(step), "ok" if rc == 0 else "FAILED (exit {})".format(rc)))
                if rc != 0:
                    failed += 1
                    print("    " + out.strip().replace("\n", "\n    "))
                    break
    for remote in unmatched:
        print("not cloned here (or not under an owner): {}".format(remote))
    return 1 if failed else 0
