# Installation and packaging

Python distributions use Hatchling 1.27+ and declare `license = "MIT"` as an [SPDX expression](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license). This removes the repeated setuptools license-table deprecation during
wheel builds, editable installs and `./install`. Python 3.8 remains supported;
the version comes from `VERSION`, and the console command remains `lantern`.

Install from the checkout into a writable prefix:

```bash
./install --prefix "$HOME/.local/opt/git-lantern" --bin-link "$HOME/.local/bin/lantern"
./install --dev --prefix "$HOME/.local/opt/git-lantern" --bin-link "$HOME/.local/bin/lantern"
```

`--dev` adds pytest, Ruff 0.15.8 and the `build` frontend. The `docs` extra is
separate: use `python3 -m pip install ".[docs]"` for MkDocs.
The runtime installation does not require documentation dependencies.

Validate distribution artifacts from a development virtualenv:

```bash
python3 -m pip install -e ".[dev]"
python3 scripts/check_package.py
```

The check builds a source archive, rebuilds its wheel in isolation with Python
warnings treated as errors, verifies license/version/runtime metadata and module
contents, and installs the wheel with development dependencies into a temporary
virtualenv. It checks the installed launcher and dependency consistency outside
the checkout. Artifacts are temporary. Package downloads require network access.
CI runs this check on Python 3.8 and 3.12.

Native package workflow settings live in the deb, rpm, Homebrew and PPA folders.
