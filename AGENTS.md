# Agent Notes

- Never add hard-coded paths to directories in any project.
- Repo: git-lantern (Python CLI).
- Use `python3` for all scripts; respect `PYTHON_BIN` overrides and keep scripts POSIX-friendly (bash).
- Prefer `rg` for search and `apply_patch` for small edits.
- CLI entrypoint lives in `src/lantern/cli.py`; table formatting in `src/lantern/table.py`.
- Docs live in `docs/` and `README.md` is the top-level entry point; keep examples current.
- Tests: `make test` or `./scripts/test.sh` (avoid network).
- Lint: `make lint` or `./scripts/lint.sh`.
- Build: `make build` or `./scripts/build.sh`.
- Avoid committing generated artifacts (man pages, build outputs); update `.gitignore` if needed.
