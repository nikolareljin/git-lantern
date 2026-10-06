import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _dry_run(tmp_path: Path, system: str) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text(f"#!/bin/sh\nprintf '%s\\n' {system}\n", encoding="utf-8")
    uname.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [str(ROOT / "install"), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_macos_defaults_are_per_user(tmp_path):
    paths = _dry_run(tmp_path, "Darwin")

    assert paths["install_root"] == f"{tmp_path}/home/.local/opt/git-lantern"
    assert paths["venv_dir"] == f"{tmp_path}/home/.local/opt/git-lantern/venv"
    assert paths["bin_link"] == f"{tmp_path}/home/.local/bin/lantern"


def test_non_macos_defaults_remain_system_paths(tmp_path):
    paths = _dry_run(tmp_path, "Linux")

    assert paths == {
        "install_root": "/opt/git-lantern",
        "venv_dir": "/opt/git-lantern/venv",
        "bin_link": "/usr/local/bin/lantern",
    }


def test_explicit_paths_override_macos_defaults(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\nprintf '%s\\n' Darwin\n", encoding="utf-8")
    uname.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [str(ROOT / "install"), "--dry-run", "--prefix", "/tmp/custom", "--bin-link", "/tmp/bin/lantern"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert dict(line.split("=", 1) for line in result.stdout.splitlines()) == {
        "install_root": "/tmp/custom",
        "venv_dir": "/tmp/custom/venv",
        "bin_link": "/tmp/bin/lantern",
    }
