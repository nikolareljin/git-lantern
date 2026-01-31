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

Create `.env` from `.env.example` if you want GitHub API access.

## Scripts

```bash
./scripts/build.sh
./scripts/test.sh
./scripts/lint.sh
./scripts/local_ci.sh
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
lantern github list --user USERNAME --output data/github.json
lantern github clone --input data/github.json --root /path/to/workspace
lantern github gists list --user USERNAME --output data/gists.json
lantern github gists update GIST_ID --file ./notes.txt --force
lantern github gists update GIST_ID --file readme.md=./README.md
lantern github gists update GIST_ID --delete old.txt --force
lantern github gists create --file ./notes.txt --description "Notes" --public
lantern github gists create --file ./notes.txt --description "Notes" --private
```

## Notes

- `--fetch` will run `git fetch --prune` on each repo to refresh remote refs.
- Ahead/behind and main-branch distance use local remote-tracking refs.
