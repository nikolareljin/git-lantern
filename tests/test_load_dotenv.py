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


def test_inline_comment_after_unquoted_value(clean_env):
    _write(clean_env / ".env", 'GITHUB_TOKEN=tok  # personal\nGITLAB_USER="a # b"\n')
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "tok"
    assert os.environ.get("GITLAB_USER") == "a # b"


def test_byte_order_mark_does_not_hide_the_first_key(clean_env):
    (clean_env / ".env").write_bytes(b"\xef\xbb\xbfGITHUB_TOKEN=tok\n")
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "tok"


def test_quoted_value_with_trailing_comment(clean_env):
    _write(clean_env / ".env", "GITHUB_TOKEN=\"tok\" # personal\nGITLAB_USER='me'  # work\n")
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "tok"
    assert os.environ.get("GITLAB_USER") == "me"


def test_first_duplicate_key_wins(clean_env):
    _write(clean_env / ".env", "GITHUB_TOKEN=first\nGITHUB_TOKEN=second\n")
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "first"


@pytest.mark.parametrize("kind", ["missing", "directory", "not-utf8"])
def test_bad_git_lantern_env_warns_and_does_not_crash(clean_env, monkeypatch, capsys, kind):
    target = clean_env / "conf.env"
    if kind == "directory":
        target.mkdir()
    elif kind == "not-utf8":
        target.write_bytes(b"GITHUB_TOKEN=\xff\xfe\n")
    monkeypatch.setenv("GIT_LANTERN_ENV", str(target))
    cli.load_dotenv()
    assert "GITHUB_TOKEN" not in os.environ
    assert "GIT_LANTERN_ENV" in capsys.readouterr().err


def test_unreadable_git_lantern_env_warns(clean_env, monkeypatch, capsys):
    target = clean_env / "conf.env"
    _write(target, "GITHUB_TOKEN=tok\n")
    monkeypatch.setenv("GIT_LANTERN_ENV", str(target))

    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", deny)
    cli.load_dotenv()
    assert "GITHUB_TOKEN" not in os.environ
    assert "Permission denied" in capsys.readouterr().err


def test_untrusted_cwd_env_is_never_opened(clean_env, monkeypatch, capsys):
    clone = clean_env / "clone"
    clone.mkdir()
    _write(clone / ".env", "GITHUB_TOKEN=x\n")
    monkeypatch.chdir(clone)
    real_open = open

    def guarded(path, *args, **kwargs):
        assert not str(path).endswith(".env"), f"opened {path}"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded)
    cli.load_dotenv()
    assert "GITHUB_TOKEN" not in os.environ
    assert capsys.readouterr().err == ""


def test_checkout_match_ignores_case_on_windows(clean_env, monkeypatch):
    monkeypatch.setattr(cli.os.path, "normcase", lambda p: p.lower())
    monkeypatch.setattr(cli, "_SRC_DIR", str(clean_env).upper() + os.sep + "src")
    _write(clean_env / ".env", "GITHUB_TOKEN=tok\n")
    cli.load_dotenv()
    assert os.environ.get("GITHUB_TOKEN") == "tok"


def test_bad_checkout_env_warns(clean_env, capsys):
    (clean_env / ".env").write_bytes(b"GITHUB_TOKEN=\xff\n")
    cli.load_dotenv()
    assert "GITHUB_TOKEN" not in os.environ
    assert ".env not read" in capsys.readouterr().err
