#!/usr/bin/env python3
"""Build and install distribution artifacts without packaging warnings."""

import email.parser
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    output = result.stdout + result.stderr
    if result.returncode or "WARNING:" in output or "DeprecationWarning" in output:
        raise RuntimeError(output)
    return result.stdout.strip()


def main():
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    env = dict(os.environ, PYTHONWARNINGS="error")
    with tempfile.TemporaryDirectory(prefix="lantern-package-check-") as directory:
        stage = Path(directory)
        # The default build creates an sdist, then rebuilds its wheel in isolation.
        run([sys.executable, "-m", "build", "--outdir", str(stage), str(root)], env=env)
        wheels = list(stage.glob("*.whl"))
        if len(wheels) != 1 or len(list(stage.glob("*.tar.gz"))) != 1:
            raise RuntimeError("Expected one wheel and one source archive")
        with zipfile.ZipFile(wheels[0]) as wheel:
            metadata_name = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = email.parser.Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
            expected = {"Version": version, "License-Expression": "MIT", "Requires-Python": ">=3.12"}
            for field, value in expected.items():
                if metadata[field] != value:
                    raise RuntimeError("Unexpected {}: {}".format(field, metadata[field]))
            if "dev" not in metadata.get_all("Provides-Extra", []):
                raise RuntimeError("The installer requires a declared dev extra")
            modules = {str(path.relative_to(root / "src")).replace(os.sep, "/")
                       for path in (root / "src/lantern").glob("*.py")}
            if not modules.issubset(wheel.namelist()):
                raise RuntimeError("Wheel is missing runtime modules")
        prefix = stage / "installed"
        run([sys.executable, "-m", "venv", str(prefix)])
        bin_dir = prefix / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        launcher = bin_dir / ("lantern.exe" if os.name == "nt" else "lantern")
        installed_env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            installed_env.pop(key, None)
        installed = {"cwd": directory, "env": installed_env}
        run([str(python), "-m", "pip", "install", str(wheels[0]) + "[dev]"], **installed)
        if run([str(launcher), "--version"], **installed) != version:
            raise RuntimeError("Installed launcher reports the wrong version")
        run([str(python), "-c", "import lantern.cli, pytest, ruff, build"], **installed)
        run([str(python), "-m", "pip", "check"], **installed)
    print("Source archive, wheel metadata, dev extra and installed CLI passed without warnings.")


if __name__ == "__main__":
    main()
