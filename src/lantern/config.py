import json
import os
from typing import Dict, List


DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/git-lantern/config.json")
ALT_USER_CONFIG_PATH = os.path.expanduser("~/.git-lantern/config.json")
SYSTEM_CONFIG_PATHS = (
    "/etc/git-lantern/config.json",
    "/usr/local/etc/git-lantern/config.json",
)


def config_path() -> str:
    override = os.environ.get("GIT_LANTERN_CONFIG", "")
    if override:
        return os.path.expanduser(override)
    for path in (ALT_USER_CONFIG_PATH, DEFAULT_CONFIG_PATH, *SYSTEM_CONFIG_PATHS):
        if os.path.isfile(path):
            return path
    return DEFAULT_CONFIG_PATH


def load_config() -> Dict:
    path = config_path()
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_server_name(config: Dict, name: str = "") -> str:
    if name:
        return name
    env_name = os.environ.get("LANTERN_SERVER", "")
    if env_name:
        return env_name
    return config.get("default_server", "") or "github.com"


def get_server(config: Dict, name: str = "") -> Dict:
    server_name = get_server_name(config, name)
    servers = config.get("servers", {}) if isinstance(config.get("servers"), dict) else {}
    server = servers.get(server_name, {}) if server_name else {}
    provider = server.get("provider") or _infer_provider(server_name)
    user = server.get("user") or server.get("USER") or ""
    token = server.get("token") or server.get("TOKEN") or ""
    merged = {"name": server_name or provider, "provider": provider, "user": user, "token": token}
    merged.update(server)
    return merged


def list_servers(config: Dict) -> List[Dict]:
    servers = config.get("servers", {}) if isinstance(config.get("servers"), dict) else {}
    output = []
    for name, server in servers.items():
        provider = server.get("provider") or _infer_provider(name)
        user = server.get("user") or server.get("USER") or ""
        output.append(
            {
                "name": name,
                "provider": provider,
                "base_url": server.get("base_url", ""),
                "user": user,
            }
        )
    return output


def _infer_provider(name: str) -> str:
    if not name:
        return "github"
    lowered = name.lower()
    if "gitlab" in lowered:
        return "gitlab"
    if "bitbucket" in lowered:
        return "bitbucket"
    return "github"
