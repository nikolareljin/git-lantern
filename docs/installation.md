# Installation and packaging

Python 3.12+ and Git are required. Builds use setuptools 77+ with `license = "MIT"`
as an [SPDX expression](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license).
This removes the repeated license-table deprecation during wheel builds, editable
installs and `./install`. The version comes from `VERSION`; the command is `lantern`.

Install from the checkout into a writable prefix:

```bash
./install --prefix "$HOME/.local/opt/git-lantern" --bin-link "$HOME/.local/bin/lantern"
./install --dev --prefix "$HOME/.local/opt/git-lantern" --bin-link "$HOME/.local/bin/lantern"
```

`--dev` adds pytest, Ruff 0.15.8 and the `build` frontend. The `docs` extra is
separate: use `python3 -m pip install ".[docs]"` for MkDocs.
The runtime installation does not require documentation dependencies.

Install Python 3.12+ before upgrading an older installation. Set `PYTHON_BIN` if
your shell's default Python is older. An existing installation virtualenv running
older Python is refused before package changes; choose a new `--prefix` or recreate
that dedicated virtualenv with Python 3.12+. A development `.venv` on older Python
is recreated by the development scripts after checking that it is a virtualenv.
An explicit `PYTHON_BIN` must name a runnable Python 3.12+ interpreter; an invalid
override fails instead of silently selecting a different interpreter.

Validate distribution artifacts from a development virtualenv:

```bash
python3 -m pip install -e ".[dev]"
python3 scripts/check_package.py
```

The check installs current pip and the build frontend in a temporary virtualenv,
then builds a source archive and rebuilds its wheel in isolation with Python
warnings treated as errors, verifies license/version/runtime metadata and module
contents, and installs the wheel with development dependencies into a temporary
virtualenv. It checks the installed launcher and dependency consistency outside
the checkout. Artifacts are temporary. Package downloads require network access.
CI runs this check on Python 3.12 and 3.13. Checkout and Python setup actions use
the Node 24 runtime to avoid Node 20 deprecation warnings.

Native package workflow settings live in the deb, rpm, Homebrew and PPA folders.
