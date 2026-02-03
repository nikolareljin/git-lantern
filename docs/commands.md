# Lantern command reference

This document explains every `lantern ...` command, the data it produces, and how to interpret it.

For step-by-step workflows, see `docs/use-cases.md`.

## Common concepts

- **Workspace root**: A directory that contains multiple Git repositories. Most commands accept `--root`. If you do not pass it, Lantern assumes the current working directory and starts scanning from there.
- **Repository discovery**: Lantern considers any directory with a `.git` folder to be a repo.
- **Depth**: `--max-depth` limits how deep Lantern traverses from `--root` (default: 6).
- **Hidden directories**: By default, directories starting with `.` are skipped. Use `--include-hidden` to include them.
- **Tables**: Output from `lantern table`, `lantern status`, etc. is a fixed-width text table.
- **JSON scans**: `lantern scan` stores the full repository records to JSON for later `table` or `report` usage.

## Shell completion

```bash
pip install -e .
source completions/lantern.bash
```

## Server configuration

Lantern can read a `config.json` file to manage multiple git servers (GitHub, GitLab, Bitbucket, or self-hosted instances).

**Config file location**:
- Checks (first match wins):
  - `GIT_LANTERN_CONFIG=/path/to/config.json` (explicit override)
  - `~/.git-lantern/config.json`
  - `~/.config/git-lantern/config.json`
  - `/etc/git-lantern/config.json`
  - `/usr/local/etc/git-lantern/config.json`

**Active server selection**:
- Use `--server NAME` on `lantern forge ...` commands to select a server for that command.
- Or set `LANTERN_SERVER=NAME` to choose a default for the session.

**Example config**:
```json
{
  "default_server": "github.com",
  "servers": {
    "github.com": {
      "provider": "github",
      "base_url": "https://api.github.com",
      "USER": "my-user",
      "TOKEN": "ghp_xxx"
    },
    "gitlab.com": {
      "provider": "gitlab",
      "base_url": "https://gitlab.com/api/v4",
      "USER": "my-user",
      "TOKEN": "glpat_xxx"
    },
    "bitbucket.org": {
      "provider": "bitbucket",
      "base_url": "https://api.bitbucket.org/2.0",
      "USER": "my-user",
      "TOKEN": "bb-token",
      "auth": { "type": "bearer" }
    },
    "10.0.0.22": {
      "provider": "gitlab",
      "base_url": "https://10.0.0.22/api/v4",
      "USER": "my-user",
      "TOKEN": "glpat_xxx"
    }
  }
}
```

**List configured servers**:
```bash
lantern servers
```

**Export server config**:
```bash
lantern config export --output git-lantern-servers.json
lantern config export --output -  # stdout
```

**Import server config**:
```bash
lantern config import --input git-lantern-servers.json
lantern config import --input git-lantern-servers.json --replace
```

**Show active config path**:
```bash
lantern config path
```

## Local repository commands

### `lantern repos`

**Purpose**: List local repositories with minimal metadata.

**What it does**:
- Scans `--root` for Git repos.
- Returns one row per repo with the repository name, filesystem path, and `origin` remote URL (if present).

**Output columns**:
- `name`: Directory name of the repo.
- `path`: Absolute path to the repo.
- `origin`: Output of `git remote get-url origin` (or `-` if missing).

**Example**:
```bash
lantern repos --root ~/workspace
```

### `lantern scan`

**Purpose**: Scan for repos and write detailed status data to JSON.

**What it does**:
- Finds all repos under `--root`.
- Optionally runs `git fetch --prune` when `--fetch` is provided.
- Builds a record per repo with branch/upstream/main-branch status fields.
- Writes JSON to `--output` (default: `data/repos.json`) or stdout if `--output` is empty.

**Record fields**:
- `name`: Repo directory name.
- `path`: Absolute path to the repo.
- `branch`: Current branch name, or `detached`.
- `upstream`: Upstream ref for the current branch (for example `origin/main`).
- `up_ahead` / `up_behind`: How many commits `HEAD` is ahead/behind its upstream.
- `main_ref`: Default branch ref inferred from remotes (for example `origin/main`).
- `main_ahead` / `main_behind`: How many commits `HEAD` is ahead/behind the default branch ref.
- `default_refs`: Comma-separated list of default refs detected across remotes.
- `origin`: `origin` remote URL (or null).

**Example**:
```bash
lantern scan --root ~/workspace --output data/repos.json --fetch
```

### `lantern status`

**Purpose**: Show a live table of repo status without writing JSON.

**What it does**:
- Runs the same scan logic as `lantern scan`.
- Prints a table with the most relevant status columns.

**Output columns**:
- `name`, `branch`, `upstream`, `up`, `main_ref`, `main`.
- `up` and `main` are shown as `N↑/M↓` for ahead/behind counts.
- `≡` means no divergence (`0↑/0↓`). The `main` column may show `≡` when it matches the upstream divergence.

**Example**:
```bash
lantern status --root ~/workspace
```

### `lantern table`

**Purpose**: Render a table from a JSON scan.

**How it works**:
- Reads `--input` (default: `data/repos.json`) created by `lantern scan`.
- If `--columns` is set, only those comma-separated columns are shown.
- Otherwise, it renders every field from the first repo record in the JSON.

**What it shows**:
- The scan fields from `lantern scan` (see the list above), formatted as a fixed-width table.
- When `up_ahead`/`up_behind` and `main_ahead`/`main_behind` are present, it renders consolidated `up` and `main` columns as `N↑/M↓` (or `≡` when clean).

**Example**:
```bash
lantern table --input data/repos.json
lantern table --input data/repos.json --columns name,branch,up_ahead,up_behind
```

### `lantern find`

**Purpose**: Filter repositories by name or remote URL.

**What it does**:
- Scans `--root` for repos.
- Filters by `--name` (substring match on repo directory name).
- Filters by `--remote` (substring match on `origin` URL).

**Output columns**:
- `name`, `path`, `origin`.

**Example**:
```bash
lantern find --root ~/workspace --name lantern
lantern find --root ~/workspace --remote github.com/my-org
```

### `lantern duplicates`

**Purpose**: Identify multiple clones pointing at the same origin URL.

**What it does**:
- Scans `--root` for repos.
- Groups repos by `origin` remote URL.
- Prints only groups with 2+ repos sharing the same origin.

**Output columns**:
- `count`: How many repos share the origin.
- `origin`: The shared origin URL.
- `paths`: A ` | ` separated list of repo paths.

**Example**:
```bash
lantern duplicates --root ~/workspace
```

### `lantern sync`

**Purpose**: Run `git fetch`, `git pull`, and/or `git push` across many repos.

**What it does**:
- If no action flags are given, it defaults to `--fetch`.
- Executes the chosen Git commands in each repo:
  - `fetch` => `git fetch --prune`
  - `pull` => `git pull --ff-only`
  - `push` => `git push`
- Adds `--only-clean` to skip repos with uncommitted changes.
- Adds `--only-upstream` to skip repos without an upstream.
- Adds `--dry-run` to print intended actions without running Git.

**Output columns**:
- `name`: Repo name.
- `result`: A summary such as `fetch:ok pull:ok`, or `skip:dirty`.
- `path`: Repo path.

**Example**:
```bash
lantern sync --root ~/workspace --fetch
lantern sync --root ~/workspace --pull --only-clean --only-upstream
lantern sync --root ~/workspace --push --dry-run
```

### `lantern report`

**Purpose**: Export scan results to CSV, JSON, or Markdown.

**What it does**:
- Reads a JSON scan (`--input`, default `data/repos.json`).
- Writes in the selected `--format`:
  - `csv` (default)
  - `json`
  - `md`
- If `--columns` is provided, only those fields are exported.
- If `--output` is empty, writes to stdout.

**Examples**:
```bash
lantern report --input data/repos.json --output data/repos.csv
lantern report --input data/repos.json --format md --output data/repos.md
lantern report --input data/repos.json --format json
```

## Git server commands (`lantern forge ...`)

The `lantern forge` command group can target GitHub, GitLab, or Bitbucket using `--server` and the config file.
It still supports `.env` fallbacks and will use the current directory if `--root` is not set.

Environment fallbacks:
- `GITHUB_USER`, `GITHUB_TOKEN`
- `GITLAB_USER`, `GITLAB_TOKEN`
- `BITBUCKET_USER`, `BITBUCKET_TOKEN`
- `LANTERN_SERVER` (for default server selection)

**Precedence**:
- CLI flags (`--user`, `--token`, `--server`)
- `.env` values (loaded from the current directory)
- `config.json` values

### `lantern forge list`

**Purpose**: List repositories from the selected git server and write to JSON.

**What it does**:
- Uses `--server` to select a provider (defaults to `default_server`, which defaults to `github.com`).
- Uses `--user` (or config/env defaults) to scope repos.
- If `--token` is set, it uses the authenticated endpoint and can include private repos owned by the user.
- If `--include-forks` is not set, forked repos are excluded.
- Writes JSON to `--output` (default `data/github.json`) or stdout.

**Output fields per repo**:
- `name`, `private`, `default_branch`, `ssh_url`, `clone_url`, `html_url`.

**Output metadata**:
- `server`, `provider`, `base_url`, `user`.

**Example**:
```bash
lantern forge list --server github.com --user my-user --output data/github.json
lantern forge list --server gitlab.com --output data/gitlab.json
lantern forge list --server bitbucket.org --output data/bitbucket.json
```

### `lantern forge clone`

**Purpose**: Clone repos from a git server list JSON.

**What it does**:
- Reads `--input` (default `data/github.json`).
- Clones each repo by its `ssh_url` into `--root`.
- Skips repos already present.
- With `--dry-run`, prints the clone commands without executing.

**Example**:
```bash
lantern forge clone --input data/github.json --root ~/workspace
```

### `lantern forge gists list` / `lantern forge snippets list`

**Purpose**: List gists/snippets and write to JSON (GitHub only).

**What it does**:
- Uses `--server` to select a GitHub server.
- Uses `--user` or `GITHUB_USER` for public gists, or uses a token to list the authenticated user's gists.
- Writes JSON to `--output` (default `data/gists.json`) or stdout.

**Output fields per gist**:
- `id`, `description`, `public`, `files`, `html_url`, `updated_at`.

**Example**:
```bash
lantern forge gists list --user my-user --output data/gists.json
```

### `lantern forge gists update` / `lantern forge snippets update`

**Purpose**: Update or delete files in an existing gist/snippet (GitHub only).

**What it does**:
- Requires a GitHub token (`--token` or `GITHUB_TOKEN`).
- `--file` can be repeated and supports `name=path` syntax.
- `--delete` can be repeated to remove files.
- If you would overwrite or delete existing files, you must provide `--force`.
- `--description` updates the gist description.

**Examples**:
```bash
lantern forge gists update GIST_ID --file ./notes.txt --force
lantern forge gists update GIST_ID --file readme.md=./README.md
lantern forge gists update GIST_ID --delete old.txt --force
```

### `lantern forge gists create` / `lantern forge snippets create`

**Purpose**: Create a new gist/snippet (GitHub only).

**What it does**:
- Requires a GitHub token (`--token` or `GITHUB_TOKEN`).
- `--file` can be repeated and supports `name=path` syntax.
- Visibility defaults to private unless `--public` is set.
- `--description` sets the gist description.

**Examples**:
```bash
lantern forge gists create --file ./notes.txt --description "Notes" --public
lantern forge gists create --file ./notes.txt --description "Notes" --private
```
