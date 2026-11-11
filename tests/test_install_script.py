from __future__ import annotations

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


def test_dry_run_warns_when_path_selects_another_install(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    active = fake_bin / "lantern"
    active.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    active.chmod(0o755)
    target = tmp_path / "new-bin" / "lantern"
    env = dict(os.environ, PATH=f"{fake_bin}:/usr/bin:/bin")
    result = subprocess.run(
        [str(ROOT / "install"), "--dry-run", "--prefix", str(tmp_path / "install"), "--bin-link", str(target)],
        capture_output=True, text=True, env=env, check=True,
    )
    assert "PATH selects another lantern installation" in result.stderr
    assert str(active) in result.stderr and str(target) in result.stderr
    assert dict(line.split("=", 1) for line in result.stdout.splitlines())["bin_link"] == str(target)


def test_dry_run_accepts_another_link_to_the_selected_install(tmp_path):
    prefix = tmp_path / "install"
    executable = prefix / "venv" / "bin" / "lantern"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "lantern").symlink_to(executable)
    env = dict(os.environ, PATH=f"{fake_bin}:/usr/bin:/bin")
    result = subprocess.run(
        [str(ROOT / "install"), "--dry-run", "--prefix", str(prefix), "--bin-link", str(tmp_path / "other-bin" / "lantern")],
        capture_output=True, text=True, env=env, check=True,
    )
    assert "PATH selects another lantern installation" not in result.stderr
