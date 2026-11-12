"""Branch cleanup policy and guarded Git operations."""

import calendar
import fnmatch
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import progress

if os.name == "nt":
    import msvcrt
else:
    import fcntl


AGES = ("30d", "60d", "90d", "6m", "1y")
KEEP = ("main", "master", "trunk", "develop", "production", "release", "release/*", "hotfix/*")


class Unsafe(RuntimeError):
    """Required cleanup evidence is missing or no longer valid."""


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Unsafe("missing or invalid activity date") from exc


def cutoff(age: str, now: datetime) -> datetime:
    if age not in AGES:
        raise ValueError("unsupported age cutoff")
    if age.endswith("d"):
        return now - timedelta(days=int(age[:-1]))
    months = 6 if age == "6m" else 12
    total = now.year * 12 + now.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    return now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))


def git(path: str, *args: str, check: bool = True, isolated: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        env.pop(key, None)
    for key in list(env):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            del env[key]
    if isolated:
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1")
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", path, *args], capture_output=True, text=True, timeout=120, check=False,
        env=env, stdin=subprocess.DEVNULL,
    )
    if check and result.returncode:
        # Git errors can contain authenticated remote URLs. Keep them out of reports.
        raise Unsafe("git {} failed (exit {})".format(args[0], result.returncode))
    return result


class Observations:
    """Serialize cleanup runs and atomically retain branch existence evidence."""

    def __init__(self, path: Optional[Path] = None):
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        self.path = path or state / "git-lantern" / "branch-observations.json"
        self.entries: Dict = {}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(str(self.path) + ".lock", "a+b")
        try:
            if os.name == "nt":
                if os.fstat(self.lock.fileno()).st_size == 0:
                    self.lock.write(b"0")
                    self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise Unsafe("another branch cleanup is running")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 1 and isinstance(data.get("branches"), dict):
                self.entries = data["branches"]
        except (OSError, ValueError, AttributeError):
            self.entries = {}
        return self

    def observe(self, key: str, sha: str, now: datetime) -> datetime:
        old = self.entries.get(key)
        first = now
        if isinstance(old, dict) and old.get("sha") == sha:
            try:
                previous = timestamp(old.get("first_seen"))
                if previous <= now:
                    first = previous
            except Unsafe:
                pass
        self.entries[key] = {"sha": sha, "first_seen": first.isoformat()}
        return first

    def forget_missing(self, prefix: str, present: List[str]) -> None:
        for key in list(self.entries):
            if key.startswith(prefix) and key not in present:
                del self.entries[key]

    def __exit__(self, exc_type, exc, tb):
        try:
            fd, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix=".observations-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"version": 1, "branches": self.entries}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        finally:
            self.lock.close()


@dataclass
class Candidate:
    name: str
    scope: str
    sha: str
    merged: bool = False
    creator: str = "unknown"
    created: str = "unknown"
    author: str = "unknown"
    committer: str = "unknown"
    changed: str = "unknown"
    activity: str = "unknown"
    first_seen: str = "unknown"
    reason: str = ""
    merge_checked: bool = False
    age_uncertain: bool = False

    @property
    def ref(self) -> str:
        return ("refs/heads/" if self.scope == "local" else "refs/remotes/origin/") + self.name

    def label(self) -> str:
        state = "merged" if self.merged else "UNMERGED"
        if self.reason and not self.merge_checked:
            state = "not checked"
        age = "UNKNOWN - manual approval required" if self.age_uncertain else "established"
        if self.reason and not self.merge_checked and not self.age_uncertain:
            age = "not checked"
        return (f"{self.scope} {self.name} [{state}] "
                f"creator={self.creator}; created={self.created}; first-seen={self.first_seen}; "
                f"creation-age={age}; "
                f"last-author={self.author}; last-committer={self.committer}; "
                f"last-commit={self.changed}; activity={self.activity}")


def checked_out(path: str) -> set:
    return {line[len("branch refs/heads/"):] for line in
            git(path, "worktree", "list", "--porcelain").stdout.splitlines()
            if line.startswith("branch refs/heads/")}


def merge_state(path: str, base: str, sha: str) -> bool:
    ancestor = git(path, "merge-base", "--is-ancestor", sha, base, check=False)
    if ancestor.returncode == 0:
        return True
    if ancestor.returncode != 1:
        raise Unsafe("cannot establish merge ancestry")
    git(path, "merge-base", base, sha)
    objects = git(path, "rev-parse", "--git-path", "objects").stdout.strip()
    objects = os.path.realpath(os.path.join(path, objects))
    object_format = git(path, "rev-parse", "--show-object-format").stdout.strip()
    base_tree = git(path, "rev-parse", base + "^{tree}").stdout.strip()
    # Use normal three-way text merging in an isolated object store. A reverse
    # patch can match the wrong repeated block and discard unmerged work.
    with tempfile.TemporaryDirectory(prefix="lantern-merge-") as directory:
        git(directory, "init", "--bare", "--template=", "--object-format=" + object_format, isolated=True)
        Path(directory, "objects", "info", "alternates").write_text(
            json.dumps(objects, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        Path(directory, "info").mkdir(exist_ok=True)
        Path(directory, "info", "attributes").write_text("* merge=text\n", encoding="utf-8")
        result = git(directory, "-c", "merge.renormalize=false", "-c", "merge.renames=false",
                     "merge-tree", "--write-tree", base, sha, isolated=True, check=False)
    if result.returncode not in (0, 1):
        raise Unsafe("cannot verify branch content against the default branch")
    return result.returncode == 0 and result.stdout.splitlines()[0] == base_tree


def local_guard(path: str, candidate: Candidate) -> None:
    if git(path, "symbolic-ref", "-q", candidate.ref, check=False).returncode == 0:
        raise Unsafe("symbolic branch ref")
    if candidate.name in checked_out(path):
        raise Unsafe("checked out in a worktree")
    if candidate.scope == "local":
        upstream = git(path, "for-each-ref", "--format=%(upstream)", candidate.ref).stdout.strip()
        if upstream != "refs/remotes/origin/" + candidate.name:
            raise Unsafe("missing or uncertain origin upstream")
        upstream_sha = git(path, "rev-parse", "--verify", upstream).stdout.strip()
        if git(path, "rev-list", "--max-count=1", upstream_sha + ".." + candidate.sha).stdout.strip():
            raise Unsafe("local branch has unpushed commits")


def scan(path, provider, observations, age, now=None, review_unknown_age=False, target=None) -> List[Candidate]:
    now = now or datetime.now(timezone.utc)
    limit = cutoff(age, now)
    progress.report("Validating the clone and origin...")
    if git(path, "rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        raise Unsafe("shallow repository: complete history is required")
    grafts = git(path, "rev-parse", "--git-path", "info/grafts").stdout.strip()
    if os.path.isfile(os.path.join(path, grafts)) and os.path.getsize(os.path.join(path, grafts)):
        raise Unsafe("repository uses grafted history")
    provider.verify_origin(git(path, "remote", "get-url", "origin").stdout.strip())
    provider.verify_origin(git(path, "remote", "get-url", "--push", "origin").stdout.strip())
    if len(git(path, "remote", "get-url", "--push", "--all", "origin").stdout.splitlines()) != 1:
        raise Unsafe("multiple push URLs")
    progress.report("Fetching current branch refs from origin...")
    git(path, "-c", "fetch.pruneTags=false", "-c", "remote.origin.pruneTags=false", "fetch",
        "--no-tags", "--refmap=", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
    progress.report("Reading default branch and server protection rules...")
    provider.refresh()
    base = git(path, "rev-parse", "--verify", "refs/remotes/origin/" + provider.default_branch).stdout.strip()
    raw = git(path, "for-each-ref", "--format=%(refname)%09%(objectname)",
              "refs/heads/", "refs/remotes/origin/").stdout
    prefix = json.dumps([provider.identity, os.path.realpath(path)], separators=(",", ":")) + ":"
    present = []
    rows = []
    lines = raw.splitlines()
    for index, line in enumerate(lines, 1):
        ref, sha = line.split("\t")
        scope = "local" if ref.startswith("refs/heads/") else "remote"
        name = ref[len("refs/heads/"):] if scope == "local" else ref[len("refs/remotes/origin/"):]
        if name == "HEAD":
            continue
        key = prefix + scope + ":" + name
        present.append(key)
        if target is not None and ref != target.ref:
            continue
        row = Candidate(name, scope, sha)
        branch_status = (f"Revalidating {scope} {name}" if target is not None
                         else f"Branch {index}/{len(lines)}: {scope} {name}")
        progress.report(branch_status + "\nChecking age and local safety rules...")
        first = observations.observe(key, sha, now)
        row.first_seen = first.isoformat()
        try:
            if name == provider.default_branch or any(fnmatch.fnmatchcase(name, pattern) for pattern in KEEP):
                raise Unsafe("protected branch name")
            if provider.protected(name):
                raise Unsafe("server-protected branch")
            local_guard(path, row)
            info = git(path, "show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%aI%n%cI", sha).stdout.splitlines()
            row.author, row.committer = info[:2]
            last_commit = max(timestamp(info[2]), timestamp(info[3]))
            row.changed = last_commit.isoformat()
            if scope == "local":
                entries = git(path, "reflog", "show", "--date=iso-strict", "--format=%gs%x00%gn <%ge>%x00%gD", ref).stdout.splitlines()
                if entries:
                    creation = entries[-1].split("\0")
                    if len(creation) == 3 and creation[0].startswith("branch: Created from ") and "@{" in creation[2]:
                        row.creator = creation[1]
                        row.created = timestamp(creation[2].rsplit("@{", 1)[1].rstrip("}")).isoformat()
            # Reflogs detect a local ref updated recently even when its commit is old.
            log = git(path, "reflog", "show", "-1", "--date=iso-strict", "--format=%gD", ref).stdout.strip()
            if "@{" in log:
                ref_changed = timestamp(log.rsplit("@{", 1)[1].rstrip("}"))
                # A tracking ref's first fetch is not evidence of server-side creation.
                tracking_creation = False
                if scope == "remote" and review_unknown_age and first >= limit:
                    entries = git(path, "reflog", "show", "--format=%gs", ref).stdout.splitlines()
                    tracking_creation = len(entries) == 1 and entries[0].endswith(": storing head")
                if not tracking_creation:
                    last_commit = max(last_commit, ref_changed)
            if first >= limit:
                if not review_unknown_age:
                    raise Unsafe("branch age not established; waiting since first observation")
                row.age_uncertain = row.created == "unknown"
            row.activity = last_commit.isoformat()
            if last_commit >= limit:
                raise Unsafe("recent branch activity")
            progress.report(branch_status + "\nChecking whether the current tip is merged...")
            row.merged = merge_state(path, base, sha)
            row.merge_checked = True
            commits = set(git(path, "rev-list", sha, "^" + base).stdout.splitlines()) | {sha}
            progress.report(branch_status + "\nChecking pull requests, discussions, and comments...")
            activity = provider.activity(name, commits)
            if activity.blocked:
                raise Unsafe(activity.blocked)
            latest = max([last_commit] + ([] if review_unknown_age else [first]) + activity.dates)
            row.activity = latest.isoformat()
            if latest >= limit:
                raise Unsafe("recent branch activity")
        except Unsafe as exc:
            row.reason = str(exc)
        rows.append(row)
    observations.forget_missing(prefix, present)
    return rows


def delete(path, provider, row: Candidate) -> None:
    local_guard(path, row)
    if git(path, "rev-parse", "--verify", row.ref).stdout.strip() != row.sha:
        raise Unsafe("branch changed since selection")
    if row.scope == "remote":
        provider.verify_origin(git(path, "remote", "get-url", "--push", "origin").stdout.strip())
        git(path, "-c", "push.followTags=false", "push", "--no-follow-tags",
            "--force-with-lease=refs/heads/{}:{}".format(row.name, row.sha),
            "origin", ":refs/heads/" + row.name)
    else:
        git(path, "update-ref", "--no-deref", "-d", "refs/heads/" + row.name, row.sha)
