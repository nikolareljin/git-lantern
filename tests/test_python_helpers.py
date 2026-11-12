import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def fake_python(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' '{}'\n".format(version), encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("version,accepted", [("3.8", False), ("3.11", False), ("3.12", True), ("3.13", True)])
def test_python_selection_requires_312(tmp_path, version, accepted):
    bash = shutil.which("bash")
    fake_python(tmp_path / "python3", version)
    fake_python(tmp_path / "python", version)
    result = subprocess.run(
        [bash, "-c", 'source "$1"; resolve_python3', "bash", str(ROOT / "scripts/python_helpers.sh")],
        env=dict(os.environ, PATH=str(tmp_path)), capture_output=True, text=True,
    )
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout.strip() == "python3"


def test_installer_refuses_old_virtualenv_before_package_changes(tmp_path):
    prefix = tmp_path / "install"
    fake_python(prefix / "venv/bin/python", "3.11")
    marker = prefix / "venv/keep.txt"
    marker.write_text("existing installation", encoding="utf-8")
    result = subprocess.run(
        [str(ROOT / "install"), "--prefix", str(prefix), "--bin-link", str(tmp_path / "bin/lantern")],
        env=dict(os.environ, PYTHON_BIN=sys.executable), capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Choose a new --prefix or recreate" in result.stderr
    assert "pip" not in result.stdout
    assert marker.read_text() == "existing installation"
