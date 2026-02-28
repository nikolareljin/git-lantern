import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lantern import config as lantern_config  # noqa: E402
from lantern import forge, github  # noqa: E402


class OrgSupportTests(unittest.TestCase):
    def test_get_server_organizations_normalizes_list_and_dict_shapes(self) -> None:
        server = {
            "organizations": [
                "org-a",
                {"name": "org-b", "token": "tok-b"},
                {"org": "org-c"},
            ]
        }
        orgs = lantern_config.get_server_organizations(server)
        self.assertEqual(
            orgs,
            [
                {"name": "org-a", "token": ""},
                {"name": "org-b", "token": "tok-b"},
                {"name": "org-c", "token": ""},
            ],
        )

        server_map = {
            "orgs": {
                "team-one": {"token": "tok-1"},
                "team-two": "tok-2",
                "team-three": {},
            }
        }
        orgs_map = lantern_config.get_server_organizations(server_map)
        self.assertEqual(
            orgs_map,
            [
                {"name": "team-one", "token": "tok-1"},
                {"name": "team-two", "token": "tok-2"},
                {"name": "team-three", "token": ""},
            ],
        )

    def test_forge_fetch_repos_passes_org_selection_to_github_backend(self) -> None:
        with patch("lantern.github.fetch_repos", return_value=[]) as mocked:
            forge.fetch_repos(
                provider="github",
                user="alice",
                token="main-token",
                include_forks=False,
                base_url="https://api.github.com",
                organizations=[{"name": "org-a", "token": "org-token"}],
                include_user=False,
            )
            mocked.assert_called_once()
            args, kwargs = mocked.call_args
            self.assertEqual(args[0], "alice")
            self.assertEqual(args[1], "main-token")
            self.assertEqual(kwargs["organizations"], [{"name": "org-a", "token": "org-token"}])
            self.assertFalse(kwargs["include_user"])

    def test_github_fetch_repos_supports_org_scopes_and_token_overrides(self) -> None:
        calls = []

        def fake_request(url: str, token: str):
            calls.append((url, token))
            split = urlsplit(url)
            page = (parse_qs(split.query).get("page") or [""])[0]
            if "/orgs/org-a/repos" in url and page == "1":
                return [
                    {
                        "name": "service-a",
                        "full_name": "org-a/service-a",
                        "private": True,
                        "fork": False,
                        "default_branch": "main",
                        "ssh_url": "git@github.com:org-a/service-a.git",
                        "clone_url": "https://github.com/org-a/service-a.git",
                        "html_url": "https://github.com/org-a/service-a",
                        "owner": {"login": "org-a"},
                    }
                ]
            if "/orgs/org-b/repos" in url and page == "1":
                return [
                    {
                        "name": "service-b",
                        "full_name": "org-b/service-b",
                        "private": False,
                        "fork": False,
                        "default_branch": "main",
                        "ssh_url": "git@github.com:org-b/service-b.git",
                        "clone_url": "https://github.com/org-b/service-b.git",
                        "html_url": "https://github.com/org-b/service-b",
                        "owner": {"login": "org-b"},
                    }
                ]
            return []

        with patch("lantern.github._request", side_effect=fake_request):
            repos = github.fetch_repos(
                user="alice",
                token="main-token",
                include_forks=False,
                base_url="https://api.github.com",
                organizations=[
                    {"name": "org-a", "token": "org-a-token"},
                    {"name": "org-b", "token": ""},
                ],
                include_user=False,
            )

        self.assertEqual([repo["name"] for repo in repos], ["org-a/service-a", "org-b/service-b"])
        # org-a uses dedicated token, org-b falls back to server token.
        org_a_calls = [entry for entry in calls if "/orgs/org-a/repos" in entry[0]]
        org_b_calls = [entry for entry in calls if "/orgs/org-b/repos" in entry[0]]
        self.assertTrue(org_a_calls)
        self.assertTrue(org_b_calls)
        self.assertEqual(org_a_calls[0][1], "org-a-token")
        self.assertEqual(org_b_calls[0][1], "main-token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
