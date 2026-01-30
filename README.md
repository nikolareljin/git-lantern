# git-lantern

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

## Commands (initial)

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
