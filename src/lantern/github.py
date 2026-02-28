import json
import os
from datetime import datetime, timezone, timedelta
import re
import subprocess
import shutil
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def _request(url: str, token: Optional[str]) -> Any:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def _is_trusted_github_host(host: str, base_url: Optional[str]) -> bool:
    host = host.lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    trusted_hosts = {
        "github.com",
        "api.github.com",
        "gist.github.com",
        "raw.githubusercontent.com",
        "gist.githubusercontent.com",
    }
    if base_url:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.hostname:
            trusted_hosts.add(parsed.hostname.lower())
    return host in trusted_hosts


def download_gist_file(raw_url: str, token: Optional[str], base_url: Optional[str] = None) -> bytes:
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme != "https":
        raise ValueError(f"Refusing to download non-HTTPS URL: {raw_url}")
    if not parsed.netloc or not _is_trusted_github_host(parsed.netloc, base_url):
        raise ValueError(f"Refusing to download from untrusted host: {parsed.netloc}")
    req = urllib.request.Request(raw_url)
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _base_url(base_url: Optional[str]) -> str:
    return (base_url or "https://api.github.com").rstrip("/")


def _is_safe_repo_component(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    # Allow namespaced identifiers like "group/subgroup/project" while
    # rejecting traversal/special components.
    if re.search(r"[^A-Za-z0-9._/-]", candidate):
        return False
    if candidate.startswith("/") or candidate.endswith("/"):
        return False
    for part in candidate.split("/"):
        if part in ("", ".", ".."):
            return False
    return True


def fetch_repos(
    user: str,
    token: Optional[str],
    include_forks: bool,
    base_url: Optional[str] = None,
    organizations: Optional[List[Dict[str, str]]] = None,
    include_user: bool = True,
) -> List[Dict]:
    per_page = 100
    repos: List[Dict] = []
    api_base = _base_url(base_url)
    seen_full_names = set()

    def _append_repo(repo: Dict, owner_filter: str, org_label: str = "") -> None:
        owner_login = str((repo.get("owner") or {}).get("login") or "")
        if owner_filter and owner_login.lower() != owner_filter.lower():
            return
        if not include_forks and repo.get("fork"):
            return
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            name = str(repo.get("name") or "").strip()
            if not name or not owner_login:
                return
            full_name = f"{owner_login}/{name}"
        if full_name in seen_full_names:
            return
        seen_full_names.add(full_name)
        repos.append(
            {
                "name": full_name,
                "private": bool(repo.get("private")),
                "default_branch": repo.get("default_branch"),
                "ssh_url": repo.get("ssh_url"),
                "clone_url": repo.get("clone_url"),
                "html_url": repo.get("html_url"),
                "owner": owner_login,
                "org": org_label,
            }
        )

    def _fetch_endpoint(
        url_base: str,
        params: Dict[str, str],
        request_token: Optional[str],
        owner_filter: str,
        org_label: str = "",
    ) -> None:
        page = 1
        while True:
            page_params = dict(params)
            page_params["page"] = str(page)
            url = f"{url_base}?{urllib.parse.urlencode(page_params)}"
            data = _request(url, request_token)
            if not data:
                break
            for repo in data:
                _append_repo(repo, owner_filter, org_label)
            page += 1

    if include_user:
        if token:
            _fetch_endpoint(
                f"{api_base}/user/repos",
                {"affiliation": "owner", "per_page": str(per_page)},
                token,
                user,
                "",
            )
        else:
            _fetch_endpoint(
                f"{api_base}/users/{user}/repos",
                {"type": "owner", "per_page": str(per_page)},
                None,
                user,
                "",
            )

    for org_entry in organizations or []:
        org_name = str((org_entry or {}).get("name") or "").strip()
        if not org_name:
            continue
        org_token = str((org_entry or {}).get("token") or "").strip() or token
        _fetch_endpoint(
            f"{api_base}/orgs/{urllib.parse.quote(org_name)}/repos",
            {"type": "all", "per_page": str(per_page)},
            org_token,
            org_name,
            org_name,
        )

    return repos


def load_env() -> Dict[str, str]:
    env = {}
    for key in (
        "GITHUB_USER",
        "GITHUB_TOKEN",
        "GITLAB_USER",
        "GITLAB_TOKEN",
        "BITBUCKET_USER",
        "BITBUCKET_TOKEN",
        "LANTERN_SERVER",
    ):
        value = os.environ.get(key, "")
        if value:
            env[key] = value
    return env


def fetch_gists(
    user: Optional[str],
    token: Optional[str],
    base_url: Optional[str] = None,
) -> List[Dict]:
    per_page = 100
    page = 1
    gists: List[Dict] = []
    api_base = _base_url(base_url)

    if token and not user:
        url_base = f"{api_base}/gists"
        params = {"per_page": str(per_page)}
    else:
        if not user:
            raise ValueError("GitHub user is required without a token.")
        url_base = f"{api_base}/users/{user}/gists"
        params = {"per_page": str(per_page)}

    while True:
        params["page"] = str(page)
        url = f"{url_base}?{urllib.parse.urlencode(params)}"
        data = _request(url, token)
        if not data:
            break
        for gist in data:
            gists.append(
                {
                    "id": gist.get("id"),
                    "description": gist.get("description") or "",
                    "public": bool(gist.get("public")),
                    "files": list((gist.get("files") or {}).keys()),
                    "html_url": gist.get("html_url"),
                    "updated_at": gist.get("updated_at"),
                }
            )
        page += 1

    return gists


def get_gist(gist_id: str, token: Optional[str], base_url: Optional[str] = None) -> Dict:
    url = f"{_base_url(base_url)}/gists/{gist_id}"
    data = _request(url, token)
    return data


def update_gist(
    gist_id: str,
    token: str,
    files: Dict[str, Optional[str]],
    description: Optional[str],
    base_url: Optional[str] = None,
) -> Dict:
    file_payload: Dict[str, Dict] = {}
    for name, content in files.items():
        if content is None:
            file_payload[name] = None
        else:
            file_payload[name] = {"content": content}
    payload = {"files": file_payload}
    if description is not None:
        payload["description"] = description
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_base_url(base_url)}/gists/{gist_id}",
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def create_gist(
    token: str,
    files: Dict[str, str],
    description: Optional[str],
    public: bool,
    base_url: Optional[str] = None,
) -> Dict:
    payload = {
        "public": public,
        "files": {name: {"content": content} for name, content in files.items()},
    }
    if description is not None:
        payload["description"] = description
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_base_url(base_url)}/gists",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def fetch_open_pull_requests(
    owner: str,
    repo: str,
    token: Optional[str],
    stale_days: int = 30,
    base_url: Optional[str] = None,
) -> List[Dict]:
    if not _is_safe_repo_component(owner) or not _is_safe_repo_component(repo):
        return []
    gh_items = fetch_open_pull_requests_via_gh(owner, repo, stale_days)
    if gh_items is not None:
        return gh_items

    api_base = _base_url(base_url)
    params = {
        "state": "open",
        "sort": "updated",
        "direction": "desc",
        "per_page": "100",
    }
    url = (
        f"{api_base}/repos/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/pulls?{urllib.parse.urlencode(params)}"
    )
    try:
        data = _request(url, token)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(stale_days, 0))
    out: List[Dict] = []
    for pr in data:
        if not isinstance(pr, dict):
            continue
        updated_raw = str(pr.get("updated_at") or "")
        updated_dt = None
        if updated_raw:
            try:
                updated_dt = datetime.strptime(updated_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                updated_dt = None
        if updated_dt and updated_dt < cutoff:
            continue
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        out.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title") or "",
                "head_ref": head.get("ref") or "",
                "updated_at": updated_raw,
                "html_url": pr.get("html_url") or "",
            }
        )
    return out


def fetch_open_pull_requests_via_gh(owner: str, repo: str, stale_days: int = 30) -> Optional[List[Dict]]:
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return None
    if not _is_safe_repo_component(owner) or not _is_safe_repo_component(repo):
        return None
    cmd = [
        gh_bin,
        "pr",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,headRefName,updatedAt,url",
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(stale_days, 0))
    out: List[Dict] = []
    for pr in data:
        if not isinstance(pr, dict):
            continue
        updated_raw = str(pr.get("updatedAt") or "")
        updated_dt = None
        if updated_raw:
            candidate = updated_raw.replace("Z", "+00:00")
            try:
                updated_dt = datetime.fromisoformat(candidate)
            except ValueError:
                updated_dt = None
        if updated_dt and updated_dt < cutoff:
            continue
        out.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title") or "",
                "head_ref": pr.get("headRefName") or "",
                "updated_at": updated_raw,
                "html_url": pr.get("url") or "",
            }
        )
    return out


def get_pr_branch_via_gh(owner: str, repo: str, pr_number: int) -> Optional[str]:
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return None
    if not _is_safe_repo_component(owner) or not _is_safe_repo_component(repo):
        return None
    cmd = [
        gh_bin,
        "pr",
        "view",
        str(pr_number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "headRefName",
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    branch = str(data.get("headRefName") or "").strip()
    return branch or None


def get_pr_branch(
    owner: str,
    repo: str,
    pr_number: int,
    token: Optional[str],
    base_url: Optional[str] = None,
) -> Optional[str]:
    if not _is_safe_repo_component(owner) or not _is_safe_repo_component(repo):
        return None
    gh_branch = get_pr_branch_via_gh(owner, repo, pr_number)
    if gh_branch:
        return gh_branch
    api_base = _base_url(base_url)
    url = (
        f"{api_base}/repos/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/pulls/{int(pr_number)}"
    )
    try:
        data = _request(url, token)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    branch = str(head.get("ref") or "").strip()
    return branch or None
