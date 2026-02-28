import base64
import json
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from . import github

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


def auth_headers(
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
    organizations: Optional[List[Dict[str, str]]] = None,
    include_user: bool = True,
) -> List[Dict]:
    provider = (provider or "github").lower()
    if provider == "github":
        if not user and include_user:
            raise ValueError("User is required for GitHub.")
        if not include_user and not organizations:
            raise ValueError("At least one organization is required when --no-user is set.")
        return github.fetch_repos(
            user or "",
            token,
            include_forks,
            base_url,
            organizations=organizations,
            include_user=include_user,
        )
    if provider == "gitlab":
        return _fetch_gitlab_repos(user, token, include_forks, base_url, auth)
    if provider == "bitbucket":
        return _fetch_bitbucket_repos(user, token, include_forks, base_url, auth)
    raise ValueError(f"Unsupported provider: {provider}")


def fetch_snippets(
    provider: str,
    user: Optional[str],
    token: Optional[str],
    base_url: str,
    auth: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    provider = (provider or "github").lower()
    if provider == "github":
        return github.fetch_gists(user, token, base_url)
    if provider == "gitlab":
        return _fetch_gitlab_snippets(user, token, base_url, auth)
    if provider == "bitbucket":
        return _fetch_bitbucket_snippets(user, token, base_url, auth)
    raise ValueError(f"Unsupported provider: {provider}")


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
    headers = auth_headers("gitlab", user, token, auth)

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
                    "name": repo.get("path_with_namespace") or repo.get("path"),
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
    headers = auth_headers("bitbucket", user, token, auth)
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


def _fetch_gitlab_snippets(
    user: Optional[str],
    token: Optional[str],
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> List[Dict]:
    if not token:
        raise ValueError("Token is required for GitLab snippets.")
    per_page = 100
    page = 1
    snippets: List[Dict] = []
    base_url = _base_url("gitlab", base_url).rstrip("/")
    headers = auth_headers("gitlab", user, token, auth)

    while True:
        params = {"per_page": str(per_page), "page": str(page)}
        url = f"{base_url}/snippets?{urllib.parse.urlencode(params)}"
        data, _headers = _request(url, headers)
        if not data:
            break
        for snippet in data:
            file_name = snippet.get("file_name") or ""
            files = [file_name] if file_name else []
            snippets.append(
                {
                    "id": snippet.get("id"),
                    "title": snippet.get("title") or "",
                    "description": snippet.get("description") or "",
                    "visibility": snippet.get("visibility") or "",
                    "files": files,
                    "raw_url": snippet.get("raw_url") or "",
                    "html_url": snippet.get("web_url") or "",
                    "updated_at": snippet.get("updated_at") or "",
                    "created_at": snippet.get("created_at") or "",
                }
            )
        page += 1
    return snippets


def _fetch_bitbucket_snippets(
    user: Optional[str],
    token: Optional[str],
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> List[Dict]:
    if not user:
        raise ValueError("Workspace is required for Bitbucket snippets.")
    base_url = _base_url("bitbucket", base_url).rstrip("/")
    headers = auth_headers("bitbucket", user, token, auth)
    url = f"{base_url}/snippets/{urllib.parse.quote(user)}?role=owner&pagelen=100"
    snippets: List[Dict] = []

    while url:
        data, _headers = _request(url, headers)
        values = data.get("values", []) if isinstance(data, dict) else []
        for snippet in values:
            links = snippet.get("links", {}) or {}
            html_url = (links.get("html") or {}).get("href", "")
            snippets.append(
                {
                    "id": snippet.get("id"),
                    "title": snippet.get("title") or "",
                    "description": "",
                    "visibility": "private" if snippet.get("is_private") else "public",
                    "files": [],
                    "html_url": html_url,
                    "updated_at": snippet.get("updated_on") or "",
                    "created_at": snippet.get("created_on") or "",
                    "workspace": user,
                }
            )
        url = data.get("next") if isinstance(data, dict) else None
    return snippets


def get_gitlab_snippet(
    snippet_id: str,
    token: str,
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> Dict:
    base_url = _base_url("gitlab", base_url).rstrip("/")
    headers = auth_headers("gitlab", None, token, auth)
    url = f"{base_url}/snippets/{urllib.parse.quote(str(snippet_id))}"
    data, _headers = _request(url, headers)
    return data if isinstance(data, dict) else {}


def get_bitbucket_snippet(
    workspace: str,
    snippet_id: str,
    token: Optional[str],
    base_url: str,
    auth: Optional[Dict[str, str]],
) -> Dict:
    base_url = _base_url("bitbucket", base_url).rstrip("/")
    headers = auth_headers("bitbucket", workspace, token, auth)
    url = f"{base_url}/snippets/{urllib.parse.quote(workspace)}/{urllib.parse.quote(str(snippet_id))}"
    data, _headers = _request(url, headers)
    return data if isinstance(data, dict) else {}


def _trusted_hosts_for(base_url: str) -> set:
    trusted = {urllib.parse.urlparse(base).netloc for base in DEFAULT_BASE_URLS.values()}
    if base_url:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.netloc:
            trusted.add(parsed.netloc)
    return trusted


def download_with_headers(url: str, headers: Dict[str, str], base_url: str = "") -> bytes:
    parsed = urllib.parse.urlparse(url)
    trusted_hosts = _trusted_hosts_for(base_url)
    if parsed.scheme != "https":
        raise ValueError(f"Refusing to download non-HTTPS URL: {url!r}")
    if not parsed.netloc or parsed.netloc not in trusted_hosts:
        raise ValueError(f"Refusing to download from untrusted host: {parsed.netloc}")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()
