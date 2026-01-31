# git-lantern

<img src="./git-lantern.png" />

Local and GitHub repository visibility toolkit. The CLI is `lantern`.

## Quick start

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
lantern --help
```

## Environment

Create `.env` from `.env.example` if you want API access. For multi-server setups (GitHub/GitLab/Bitbucket), use `~/.config/git-lantern/config.json` and see `docs/commands.md` for the format.

## Shell completion

Lantern ships bash completion via argcomplete.

```bash
pip install -e .
source completions/lantern.bash
```

## Scripts

```bash
./scripts/build.sh
./scripts/test.sh
./scripts/lint.sh
./scripts/local_ci.sh
./scripts/install_git_hooks.sh
./scripts/bump_version.sh patch
./scripts/update_submodules.sh
./scripts/generate_man.sh
./scripts/packaging_init.sh
./scripts/release.sh
./scripts/release.sh --no-tag
./scripts/release.sh --tag-prefix v
```

## Makefile shortcuts

```bash
make build
make test
make lint
make ci
make bump
make submodules
make man
make packaging
make release
```

## Local testing

```bash
make lint
make test
make ci
```

If your system only provides `python3`, export `PYTHON_BIN=python3` before running the scripts.

## Git hooks

Install the pre-commit hook to run local CI before commits:

```bash
./scripts/install_git_hooks.sh
```

Skip or speed up:

```bash
SKIP_LANTERN_PRECOMMIT=1 git commit -m "..."
LANTERN_PRECOMMIT_FAST=1 git commit -m "..."
```

## CI workflows

- `.github/workflows/ci.yml` uses ci-helpers Python CI.
- Packaging workflows (Homebrew, deb, rpm, PPA) are wired to ci-helpers presets.
- `.github/workflows/release.yml` builds dist tarballs and publishes a GitHub release.
- `release.yml` supports tags like `1.2.3` or `v1.2.3` and can publish Homebrew when enabled.
- `release.yml` auto-publishes Homebrew on tag pushes and attaches deb/rpm artifacts.
- `.github/workflows/auto-tag.yml` tags release merges from `release/X.Y.Z` or `release/X.Y.Z-rcN` branches.
- Release notes are generated via ci-helpers and updated on every publish; man pages are generated during release builds.
- Update the Homebrew `homepage` and tap/release inputs before publishing.
- PPA publishing requires `PPA_GPG_PRIVATE_KEY`, `PPA_GPG_PASSPHRASE`, and `PPA_LAUNCHPAD_SSH_KEY` secrets.

## Commands (initial)

See [docs/commands.md](docs/commands.md) for the full command reference and detailed explanations of each `lantern ...` command.

```bash
lantern repos
lantern scan --root /path/to/workspace --output data/repos.json
lantern status --root /path/to/workspace
lantern table --input data/repos.json
lantern find --root /path/to/workspace --name my-repo
lantern repos --root /path/to/workspace
lantern duplicates --root /path/to/workspace
lantern sync --root /path/to/workspace --fetch
lantern sync --root /path/to/workspace --pull --only-clean --only-upstream
lantern report --input data/repos.json --output data/repos.csv
lantern report --input data/repos.json --output data/repos.md --format md
lantern forge list --user USERNAME --output data/github.json
lantern forge clone --input data/github.json --root /path/to/workspace
lantern forge gists list --user USERNAME --output data/gists.json
lantern forge gists update GIST_ID --file ./notes.txt --force
lantern forge gists update GIST_ID --file readme.md=./README.md
lantern forge gists update GIST_ID --delete old.txt --force
lantern forge gists create --file ./notes.txt --description "Notes" --public
lantern forge gists create --file ./notes.txt --description "Notes" --private
```

## Notes

- `--fetch` will run `git fetch --prune` on each repo to refresh remote refs.
- Ahead/behind and main-branch distance use local remote-tracking refs.
