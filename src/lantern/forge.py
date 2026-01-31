import base64
import json
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple


DEFAULT_BASE_URLS = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
    "bitbucket": "https://api.bitbucket.org/2.0",
}


def _request(url: str, headers: Dict[str, str]) -> Tuple[object, Dict[str, str]]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data), dict(resp.headers)


def _auth_headers(
    provider: str,
    user: Optional[str],
    token: Optional[str],
    auth: Optional[Dict[str, str]],
) -> Dict[str, str]:
    if not token:
        return {}
    if provider == "gitlab":
        return {"PRIVATE-TOKEN": token}
    if provider == "bitbucket":
        auth_type = (auth or {}).get("type", "bearer").lower()
        if auth_type == "basic":
            if not user:
                return {}
            encoded = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("utf-8")
            return {"Authorization": f"Basic {encoded}"}
        return {"Authorization": f"Bearer {token}"}
    return {"Authorization": f"token {token}"}


def _base_url(provider: str, base_url: str) -> str:
    return base_url or DEFAULT_BASE_URLS.get(provider, "")


def fetch_repos(
    provider: str,
    user: Optional[str],
    token: Optional[str],
    include_forks: bool,
    base_url: str,
    auth: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    provider = (provider or "github").lower()
    if provider == "github":
        return _fetch_github_repos(user, token, include_forks, base_url)
    if provider == "gitlab":
        return _fetch_gitlab_repos(user, token, include_forks, base_url, auth)
    if provider == "bitbucket":
        return _fetch_bitbucket_repos(user, token, include_forks, base_url, auth)
    raise ValueError(f"Unsupported provider: {provider}")


def _fetch_github_repos(
    user: Optional[str],
    token: Optional[str],
    include_forks: bool,
    base_url: str,
) -> List[Dict]:
    if not user:
        raise ValueError("User is required for GitHub.")
    per_page = 100
    page = 1
    repos: List[Dict] = []
    base_url = _base_url("github", base_url)

    if token:
        url_base = f"{base_url.rstrip('/')}/user/repos"
        params = {"affiliation": "owner", "per_page": str(per_page)}
    else:
        url_base = f"{base_url.rstrip('/')}/users/{user}/repos"
        params = {"type": "owner", "per_page": str(per_page)}

    while True:
        params["page"] = str(page)
        url = f"{url_base}?{urllib.parse.urlencode(params)}"
        data, _headers = _request(url, _auth_headers("github", user, token, None))
        if not data:
            break
        for repo in data:
            if user and repo.get("owner", {}).get("login") != user:
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


def _fetch_gitlab_repos(
    user: Optional[str],
    token: Optional[str],
    include_forks: bool,
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> List[Dict]:
    if not user and not token:
        raise ValueError("User is required without a token.")
    per_page = 100
    page = 1
    repos: List[Dict] = []
    base_url = _base_url("gitlab", base_url).rstrip("/")
    headers = _auth_headers("gitlab", user, token, auth)

    if token and not user:
        url_base = f"{base_url}/projects"
        base_params = {"membership": "true", "per_page": str(per_page)}
    else:
        url_base = f"{base_url}/users/{urllib.parse.quote(user or '')}/projects"
        base_params = {"per_page": str(per_page)}

    while True:
        params = dict(base_params)
        params["page"] = str(page)
        url = f"{url_base}?{urllib.parse.urlencode(params)}"
        data, _headers = _request(url, headers)
        if not data:
            break
        for repo in data:
            if not include_forks and repo.get("forked_from_project"):
                continue
            repos.append(
                {
                    "name": repo.get("path"),
                    "private": repo.get("visibility") != "public",
                    "default_branch": repo.get("default_branch"),
                    "ssh_url": repo.get("ssh_url_to_repo"),
                    "clone_url": repo.get("http_url_to_repo"),
                    "html_url": repo.get("web_url"),
                }
            )
        page += 1
    return repos


def _fetch_bitbucket_repos(
    user: Optional[str],
    token: Optional[str],
    include_forks: bool,
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> List[Dict]:
    if not user:
        raise ValueError("User is required for Bitbucket.")
    base_url = _base_url("bitbucket", base_url).rstrip("/")
    headers = _auth_headers("bitbucket", user, token, auth)
    url = f"{base_url}/repositories/{urllib.parse.quote(user)}?pagelen=100"
    repos: List[Dict] = []

    while url:
        data, _headers = _request(url, headers)
        values = data.get("values", []) if isinstance(data, dict) else []
        for repo in values:
            if not include_forks and repo.get("parent"):
                continue
            clones = repo.get("links", {}).get("clone", [])
            ssh_url = ""
            clone_url = ""
            for clone in clones:
                if clone.get("name") == "ssh":
                    ssh_url = clone.get("href", "")
                if clone.get("name") == "https":
                    clone_url = clone.get("href", "")
            repos.append(
                {
                    "name": repo.get("name"),
                    "private": bool(repo.get("is_private")),
                    "default_branch": (repo.get("mainbranch") or {}).get("name"),
                    "ssh_url": ssh_url,
                    "clone_url": clone_url,
                    "html_url": (repo.get("links", {}).get("html") or {}).get("href"),
                }
            )
        url = data.get("next") if isinstance(data, dict) else None
    return repos
