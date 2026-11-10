"""Complete provider evidence for branch cleanup. Missing evidence refuses deletion."""

import fnmatch
import json
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List

from . import forge
from .cleanup import Unsafe, timestamp


@dataclass
class Activity:
    dates: List = field(default_factory=list)
    blocked: str = ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unsafe("API redirect refused")


def repo_url(value):
    match = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value)
    if match and "://" not in value:
        host, path = match.groups()
    else:
        parsed = urllib.parse.urlsplit(value)
        host, path = parsed.hostname, parsed.path
    path = path.strip("/")
    return (host or "").lower(), path[:-4] if path.endswith(".git") else path


class Provider:
    def __init__(self, provider, base_url, user, token, auth, repo):
        if provider not in forge.DEFAULT_BASE_URLS:
            raise Unsafe("unsupported forge provider")
        self.provider = provider
        self.base = (base_url or forge.DEFAULT_BASE_URLS[provider]).rstrip("/")
        if urllib.parse.urlsplit(self.base).scheme != "https":
            raise Unsafe("cleanup requires an HTTPS API endpoint")
        self.repo = repo
        self.identity = self.base + "/" + repo
        self.headers = forge.auth_headers(provider, user, token, auth)
        if provider == "github" and not token:
            host = urllib.parse.urlsplit(self.base).hostname
            host = "github.com" if host == "api.github.com" else host
            try:
                result = subprocess.run(["gh", "auth", "token", "--hostname", host],
                                        capture_output=True, text=True, timeout=15, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    self.headers = forge.auth_headers(provider, user, result.stdout.strip(), auth)
            except (OSError, subprocess.TimeoutExpired):
                pass
        encoded = urllib.parse.quote(repo, safe="/" if provider != "gitlab" else "")
        self.root = self.base + ("/projects/" if provider == "gitlab" else
                                 "/repositories/" if provider == "bitbucket" else "/repos/") + encoded
        self.default_branch = ""
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, url, data=None):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme, parsed.netloc) != urllib.parse.urlsplit(self.base)[:2]:
            raise Unsafe("untrusted API pagination URL")
        req = urllib.request.Request(url, headers=self.headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=30) as response:
                payload = json.load(response)
                headers = {key.lower(): value for key, value in response.headers.items()}
                return payload, headers
        except (OSError, ValueError) as exc:
            raise Unsafe("provider API request failed; cleanup refused") from exc

    def pages(self, endpoint):
        url = endpoint if endpoint.startswith("https://") else self.root + endpoint
        separator = "&" if "?" in url else "?"
        url += separator + ("pagelen=100" if self.provider == "bitbucket" else "per_page=100&page=1")
        seen = set()
        result = []
        while url:
            if url in seen:
                raise Unsafe("API pagination loop")
            seen.add(url)
            data, headers = self.request(url)
            if self.provider == "bitbucket":
                if not isinstance(data, dict) or not isinstance(data.get("values"), list):
                    raise Unsafe("incomplete provider collection")
                items = data["values"]
                following = data.get("next")
            else:
                if not isinstance(data, list):
                    raise Unsafe("incomplete provider collection")
                items = data
                following = None
                for link in headers.get("link", "").split(","):
                    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                    if match:
                        following = match.group(1)
                if self.provider == "gitlab" and headers.get("x-next-page"):
                    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
                    query["page"] = headers["x-next-page"]
                    following = url.split("?", 1)[0] + "?" + urllib.parse.urlencode(query)
                # Some reverse proxies strip pagination headers. Probe a full final page.
                if not following and len(items) == 100:
                    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
                    query["page"] = str(int(query.get("page", "1")) + 1)
                    following = url.split("?", 1)[0] + "?" + urllib.parse.urlencode(query)
            if any(not isinstance(item, dict) for item in items):
                raise Unsafe("invalid provider collection item")
            result.extend(items)
            url = following
        return result

    def verify_origin(self, origin):
        host, path = repo_url(origin)
        api_host = urllib.parse.urlsplit(self.base).hostname
        expected_host = {"api.github.com": "github.com", "api.bitbucket.org": "bitbucket.org"}.get(api_host, api_host)
        if host != expected_host or path.lower() != self.repo.lower():
            raise Unsafe("origin does not match the selected server/repository")

    def refresh(self):
        meta, _ = self.request(self.root)
        if not isinstance(meta, dict):
            raise Unsafe("repository metadata unavailable")
        if meta.get("archived") or meta.get("disabled"):
            raise Unsafe("repository is archived or disabled")
        self.meta = meta
        if self.provider == "github":
            self.default_branch = meta.get("default_branch")
            self.branches = self.pages("/branches")
            self.prs = []
        elif self.provider == "gitlab":
            self.default_branch = meta.get("default_branch")
            self.branches = self.pages("/repository/branches")
            # Project MR lists cover target projects only; include outgoing fork MRs.
            self.prs = self.pages(self.base + "/merge_requests?state=all&scope=all")
        else:
            self.default_branch = (meta.get("mainbranch") or {}).get("name")
            self.branches = self.pages("/refs/branches")
            self.rules = self.pages("/branch-restrictions")
            self.prs = self.bitbucket_network_prs(meta)
        if not isinstance(self.default_branch, str) or not self.default_branch:
            raise Unsafe("default branch unavailable")
        repository_id = meta.get("uuid" if self.provider == "bitbucket" else "id")
        if not repository_id:
            raise Unsafe("stable repository identity unavailable")
        self.identity = self.base + "/" + self.repo + "#" + str(repository_id)

    def protected(self, name):
        branches = [b for b in self.branches if b.get("name") == name]
        if self.provider != "bitbucket":
            if len(branches) != 1 or not isinstance(branches[0].get("protected"), bool):
                raise Unsafe("branch protection unknown")
            return branches[0]["protected"]
        for rule in self.rules:
            if rule.get("branch_match_kind", "glob") != "glob" or not isinstance(rule.get("pattern"), str):
                raise Unsafe("unsupported Bitbucket branching-model restriction")
            if fnmatch.fnmatchcase(name, rule["pattern"]):
                return True
        return False

    def add_comments(self, activity, comments):
        for comment in comments:
            if comment.get("deleted") is True:
                continue
            if comment.get("system") is True:
                continue
            dates = [comment[key] for key in ("created_at", "updated_at", "created_on", "updated_on")
                     if comment.get(key)]
            if not dates:
                raise Unsafe("comment activity date unavailable")
            activity.dates.extend(timestamp(value) for value in dates)
            if comment.get("resolvable") is True and comment.get("resolved") is not True:
                activity.blocked = "unresolved review discussion"

    @property
    def graphql_endpoint(self):
        return self.base[:-len("/api/v3")] + "/api/graphql" if self.base.endswith("/api/v3") else self.base + "/graphql"

    def github_associated_prs(self, name):
        owner, repo = self.repo.split("/", 1)
        query = ("query($owner:String!,$repo:String!,$ref:String!,$cursor:String){repository(owner:$owner,name:$repo){"
                 "ref(qualifiedName:$ref){associatedPullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){"
                 "nodes{number repository{nameWithOwner} headRepository{nameWithOwner} headRefName state}"
                 "pageInfo{hasNextPage endCursor}}}}}")
        cursor = None
        seen = set()
        result = []
        while True:
            data, _ = self.request(self.graphql_endpoint, {"query": query, "variables": {
                "owner": owner, "repo": repo, "ref": "refs/heads/" + name, "cursor": cursor}})
            try:
                if data.get("errors"):
                    raise KeyError("GraphQL errors")
                connection = data["data"]["repository"]["ref"]["associatedPullRequests"]
                for node in connection["nodes"]:
                    target = node["repository"]["nameWithOwner"]
                    source = node["headRepository"]["nameWithOwner"]
                    pr, _ = self.request(self.base + "/repos/" + urllib.parse.quote(target, safe="/") + "/pulls/" + str(node["number"]))
                    if not isinstance(pr, dict) or node["state"] not in ("OPEN", "CLOSED", "MERGED"):
                        raise KeyError("PR metadata unavailable")
                    if (pr.get("head") or {}).get("ref") != name or source.lower() != self.repo.lower():
                        raise KeyError("PR association mismatch")
                    pr["_cleanup_target"] = target
                    result.append(pr)
                info = connection["pageInfo"]
                if info["hasNextPage"] is False:
                    return result
                cursor = info["endCursor"]
                if not cursor or cursor in seen:
                    raise KeyError("missing cursor")
                seen.add(cursor)
            except (KeyError, TypeError, AttributeError) as exc:
                raise Unsafe("branch PR associations unavailable") from exc

    def bitbucket_network_prs(self, meta):
        pending = [meta.get("full_name") or self.repo]
        seen = set()
        result = []
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            root = self.base + "/repositories/" + urllib.parse.quote(name, safe="/")
            details, _ = self.request(root)
            if not isinstance(details, dict) or not details.get("uuid"):
                raise Unsafe("fork network metadata unavailable")
            parent = details.get("parent")
            if parent:
                if not parent.get("full_name"):
                    raise Unsafe("fork parent unavailable")
                pending.append(parent["full_name"])
            for fork in self.pages(root + "/forks"):
                if not fork.get("full_name"):
                    raise Unsafe("fork identity unavailable")
                pending.append(fork["full_name"])
            for pr in self.pages(root + "/pullrequests?state=OPEN&state=MERGED&state=DECLINED&state=SUPERSEDED"):
                pr["_cleanup_target"] = name
                result.append(pr)
        return result

    def github_threads(self, number, target=None):
        owner, repo = (target or self.repo).split("/", 1)
        query = ("query($owner:String!,$repo:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$repo){"
                 "pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{isResolved}"
                 "pageInfo{hasNextPage endCursor}}}}}")
        cursor = None
        seen = set()
        while True:
            data, _ = self.request(self.graphql_endpoint, {"query": query, "variables": {
                "owner": owner, "repo": repo, "number": number, "cursor": cursor}})
            try:
                if data.get("errors"):
                    raise KeyError("GraphQL errors")
                threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
                nodes, info = threads["nodes"], threads["pageInfo"]
                if any(not isinstance(n.get("isResolved"), bool) for n in nodes):
                    raise KeyError("missing resolution")
                if any(n["isResolved"] is False for n in nodes):
                    return True
                if info["hasNextPage"] is False:
                    return False
                cursor = info["endCursor"]
                if not cursor or cursor in seen:
                    raise KeyError("missing cursor")
                seen.add(cursor)
            except (KeyError, TypeError, AttributeError) as exc:
                raise Unsafe("review-thread evidence unavailable") from exc

    def activity(self, name, commits):
        activity = Activity()
        prs = self.github_associated_prs(name) if self.provider == "github" else self.prs
        for pr in prs:
            if self.provider == "github":
                head = pr.get("head") or {}
                if head.get("ref") != name:
                    continue
                source = (head.get("repo") or {}).get("full_name")
                if not source:
                    raise Unsafe("PR source repository unknown")
                if source.lower() != self.repo.lower():
                    continue
                if pr.get("state") == "open":
                    return Activity(blocked="open or Draft PR")
                if pr.get("state") != "closed":
                    raise Unsafe("PR state unknown")
                number = pr["number"]
                target = pr.get("_cleanup_target", self.repo)
                pr_root = self.base + "/repos/" + urllib.parse.quote(target, safe="/")
                if self.github_threads(number, target):
                    return Activity(blocked="unresolved review discussion")
                self.add_comments(activity, self.pages(pr_root + f"/issues/{number}/comments"))
                self.add_comments(activity, self.pages(pr_root + f"/pulls/{number}/comments"))
                reviews = self.pages(pr_root + f"/pulls/{number}/reviews")
                for review in reviews:
                    if review.get("state") == "PENDING":
                        return Activity(blocked="pending PR review")
                    if review.get("submitted_at"):
                        activity.dates.append(timestamp(review["submitted_at"]))
                pr_commits = self.pages(pr_root + f"/pulls/{number}/commits")
                if not isinstance(pr.get("commits"), int) or len(pr_commits) != pr["commits"]:
                    raise Unsafe("PR commit history incomplete")
                commits.update(c["sha"] for c in pr_commits)
                if pr.get("merged_at"):
                    activity.dates.append(timestamp(pr["merged_at"]))
            elif self.provider == "gitlab":
                if pr.get("source_branch") != name:
                    continue
                if not pr.get("source_project_id") or not self.meta.get("id"):
                    raise Unsafe("MR source repository unknown")
                if pr["source_project_id"] != self.meta["id"]:
                    continue
                if pr.get("state") == "opened":
                    return Activity(blocked="open or Draft MR")
                if pr.get("state") not in ("closed", "merged"):
                    raise Unsafe("MR state unknown")
                number = pr["iid"]
                target = pr.get("target_project_id")
                if not isinstance(target, int):
                    raise Unsafe("MR target repository unavailable")
                mr_root = self.base + "/projects/" + str(target)
                for discussion in self.pages(mr_root + f"/merge_requests/{number}/discussions"):
                    if not isinstance(discussion.get("notes"), list):
                        raise Unsafe("MR discussion incomplete")
                    self.add_comments(activity, discussion["notes"])
                commits.update(c["id"] for c in self.pages(mr_root + f"/merge_requests/{number}/commits"))
                if pr.get("merged_at"):
                    activity.dates.append(timestamp(pr["merged_at"]))
            else:
                source = pr.get("source") or {}
                if (source.get("branch") or {}).get("name") != name:
                    continue
                source_id = (source.get("repository") or {}).get("uuid")
                if not source_id or not self.meta.get("uuid"):
                    raise Unsafe("PR source repository unknown")
                if source_id != self.meta["uuid"]:
                    continue
                if pr.get("state") == "OPEN":
                    return Activity(blocked="open or Draft PR")
                if pr.get("state") not in ("MERGED", "DECLINED", "SUPERSEDED"):
                    raise Unsafe("PR state unknown")
                number = pr["id"]
                target = pr.get("_cleanup_target", self.repo)
                pr_root = self.base + "/repositories/" + urllib.parse.quote(target, safe="/")
                comments = self.pages(pr_root + f"/pullrequests/{number}/comments")
                self.add_comments(activity, comments)
                # Bitbucket comments do not consistently expose resolution across versions.
                # Refuse ambiguous discussions rather than treating them as resolved.
                if any(not c.get("deleted") and not c.get("resolution") for c in comments):
                    activity.blocked = "unresolved or unknown Bitbucket discussion resolution"
                commits.update(c["hash"] for c in self.pages(pr_root + f"/pullrequests/{number}/commits"))
                if pr.get("updated_on"):
                    activity.dates.append(timestamp(pr["updated_on"]))
        for sha in commits:
            encoded = urllib.parse.quote(sha, safe="")
            if self.provider == "github":
                self.add_comments(activity, self.pages(f"/commits/{encoded}/comments"))
            elif self.provider == "gitlab":
                for discussion in self.pages(f"/repository/commits/{encoded}/discussions"):
                    if not isinstance(discussion.get("notes"), list):
                        raise Unsafe("commit discussion incomplete")
                    self.add_comments(activity, discussion["notes"])
            else:
                self.add_comments(activity, self.pages(f"/commit/{encoded}/comments"))
        return activity
