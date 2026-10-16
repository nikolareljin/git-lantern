import os

import pytest

from lantern import cli

_ALL = ["PATH", "GIT_SSH_COMMAND", "GH_HOST", "GITHUB_TOKEN", "GITLAB_USER",
        "LANTERN_SERVER", "GIT_LANTERN_ENV", "GIT_LANTERN_CONFIG",
        "LANTERN_PORTFOLIO_API_URL", "github_token"]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in _ALL:
        if key != "PATH":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.chdir(tmp_path)
    # Treat tmp_path as lantern's own checkout unless a test says otherwise.
    monkeypatch.setattr(cli, "_SRC_DIR", str(tmp_path / "src"))
    return tmp_path


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))


def test_untrusted_keys_in_cwd_env_are_ignored(clean_env, monkeypatch):
    monkeypatch.delenv("PATH")
    _write(clean_env / ".env",
           "PATH=/tmp/evil\nGIT_SSH_COMMAND=touch /tmp/pwned\nGH_HOST=evil.example\nGITHUB_TOKEN=tok\n")
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "tok"
    assert "PATH" not in os.environ
    assert "GIT_SSH_COMMAND" not in os.environ
    assert "GH_HOST" not in os.environ


def test_existing_value_is_not_overridden(clean_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-shell")
    _write(clean_env / ".env", "GITHUB_TOKEN=from-file\n")
    cli.load_dotenv()
    assert os.environ["GITHUB_TOKEN"] == "from-shell"


def test_git_lantern_env_path_is_used(clean_env, monkeypatch):
    other = clean_env / "conf.env"
    _write(other, "LANTERN_SERVER=gitlab.com\nGIT_SSH_COMMAND=x\n")
    _write(clean_env / ".env", "LANTERN_SERVER=github.com\n")
    monkeypatch.setenv("GIT_LANTERN_ENV", str(other))
    cli.load_dotenv()
    assert os.environ["LANTERN_SERVER"] == "gitlab.com"
    assert "GIT_SSH_COMMAND" not in os.environ


def test_syntax_variants(clean_env):
    _write(clean_env / ".env",
           "# comment\r\n\r\nexport GITHUB_TOKEN=\"quoted\"\r\n"
           "GITLAB_USER='me'\r\ngithub_token=lower\r\nexport PATH=/tmp/evil\r\n")
    cli.load_dotenv()
    assert os.environ["GITHUB_TOKEN"] == "quoted"
    assert os.environ["GITLAB_USER"] == "me"
    assert "github_token" not in os.environ
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_empty_file_is_a_no_op(clean_env):
    _write(clean_env / ".env", "")
    cli.load_dotenv()
    assert "GITHUB_TOKEN" not in os.environ


def test_redirecting_keys_are_ignored(clean_env):
    # These would send tokens or PR data to a host the clone chooses.
    _write(clean_env / ".env",
           "GIT_LANTERN_CONFIG=./evil.json\nLANTERN_PORTFOLIO_API_URL=http://evil\nGITHUB_TOKEN=tok\n")
    cli.load_dotenv()
    assert "GIT_LANTERN_CONFIG" not in os.environ
    assert "LANTERN_PORTFOLIO_API_URL" not in os.environ
    assert os.environ["GITHUB_TOKEN"] == "tok"


def test_cwd_env_outside_lantern_checkout_is_ignored(clean_env, monkeypatch):
    clone = clean_env / "clone"
    clone.mkdir()
    _write(clone / ".env", "LANTERN_SERVER=evil\nGITHUB_TOKEN=attacker\n")
    monkeypatch.chdir(clone)
    cli.load_dotenv()
    assert "LANTERN_SERVER" not in os.environ
    assert "GITHUB_TOKEN" not in os.environ


def test_git_lantern_env_loads_from_any_cwd(clean_env, monkeypatch):
    clone = clean_env / "clone"
    clone.mkdir()
    _write(clone / ".env", "LANTERN_SERVER=gitlab.com\nGITHUB_TOKEN=mine\n")
    monkeypatch.chdir(clone)
    monkeypatch.setenv("GIT_LANTERN_ENV", str(clone / ".env"))
    cli.load_dotenv()
    assert os.environ["LANTERN_SERVER"] == "gitlab.com"
    assert os.environ["GITHUB_TOKEN"] == "mine"
