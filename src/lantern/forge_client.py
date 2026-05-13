"""Minimal HTTP client for the forge-mind portfolio API."""

import json
import urllib.error
import urllib.request
from typing import Set


def fetch_frozen_repos(forge_url: str, timeout: int = 10) -> Set[str]:
    """Return the set of frozen repo full_names from forge-mind's fleet status endpoint.

    Raises ``urllib.error.URLError``, ``TimeoutError``, ``json.JSONDecodeError``, or
    ``OSError`` when the endpoint cannot be queried or the response is malformed.
    Callers are responsible for catching those failures and deciding whether to continue.
    """
    url = f"{forge_url.rstrip('/')}/api/v1/fleet/status"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        raise

    frozen: Set[str] = set()
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if entry.get("is_frozen"):
                full_name = str(entry.get("external_repo_full_name") or "").strip()
                if full_name:
                    frozen.add(full_name.lower())
    return frozen
