import json
import os
import urllib.parse
import urllib.request
from typing import Dict, List, Optional


def _request(url: str, token: Optional[str]) -> List[Dict]:
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
        if parsed.netloc:
            trusted_hosts.add(parsed.netloc.lower())
    return host in trusted_hosts


def download_gist_file(raw_url: str, token: Optional[str], base_url: Optional[str] = None) -> bytes:
    req = urllib.request.Request(raw_url)
    parsed = urllib.parse.urlparse(raw_url)
    if token and parsed.netloc and _is_trusted_github_host(parsed.netloc, base_url):
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _base_url(base_url: Optional[str]) -> str:
    return (base_url or "https://api.github.com").rstrip("/")


def fetch_repos(
    user: str,
    token: Optional[str],
    include_forks: bool,
    base_url: Optional[str] = None,
) -> List[Dict]:
    per_page = 100
    page = 1
    repos: List[Dict] = []
    api_base = _base_url(base_url)

    if token:
        url_base = f"{api_base}/user/repos"
        params = {
            "affiliation": "owner",
            "per_page": str(per_page),
        }
    else:
        url_base = f"{api_base}/users/{user}/repos"
        params = {
            "type": "owner",
            "per_page": str(per_page),
        }

    while True:
        params["page"] = str(page)
        url = f"{url_base}?{urllib.parse.urlencode(params)}"
        data = _request(url, token)
        if not data:
            break
        for repo in data:
            if repo.get("owner", {}).get("login") != user:
                continue
            if not include_forks and repo.get("fork"):
                continue
            repos.append(
                {
                    "name": repo.get("name"),
                    "private": bool(repo.get("private")),
                    "default_branch": repo.get("default_branch"),
                    "ssh_url": repo.get("ssh_url"),
                    "clone_url": repo.get("clone_url"),
                    "html_url": repo.get("html_url"),
                }
            )
        page += 1

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
