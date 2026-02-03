# Lantern use cases

This guide focuses on real workflows. For the full command reference, see
`docs/commands.md`.

## Server config (GitHub, GitLab, Bitbucket)

Lantern loads server settings from JSON config. Use a per-user config file:
- `~/.git-lantern/config.json` (preferred)
- `~/.config/git-lantern/config.json`

You can also set `GIT_LANTERN_CONFIG=/path/to/config.json`.

Example config:
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
    }
  }
}
```

Show the active config path:
```bash
lantern config path
```

Export/import server config (for moving settings between machines):
```bash
lantern config export --output git-lantern-servers.json
lantern config import --input git-lantern-servers.json
lantern config import --input git-lantern-servers.json --replace
```

## Local repo status and sync

### `lantern repos`
List local repos with minimal metadata.
```bash
lantern repos --root ~/workspace
```

### `lantern scan`
Create a JSON snapshot of all repos.
```bash
lantern scan --root ~/workspace --output data/repos.json --fetch
```

### `lantern status`
Live table of repo status.
```bash
lantern status --root ~/workspace --fetch
```

### `lantern table`
Render a table from a JSON scan.
```bash
lantern table --input data/repos.json
lantern table --input data/repos.json --columns name,branch,up_ahead,up_behind
```

### `lantern find`
Find repos by name or remote URL.
```bash
lantern find --root ~/workspace --name lantern
lantern find --root ~/workspace --remote gitlab.com
```

### `lantern duplicates`
Find duplicated clones that share the same origin URL.
```bash
lantern duplicates --root ~/workspace
```

### `lantern sync`
Fetch/pull/push across repos.
```bash
lantern sync --root ~/workspace --fetch
lantern sync --root ~/workspace --pull --only-clean --only-upstream
lantern sync --root ~/workspace --push --only-clean
```

### `lantern report`
Export scan data to CSV/JSON/Markdown.
```bash
lantern report --input data/repos.json --output data/repos.csv
lantern report --input data/repos.json --output data/repos.md --format md
```

## GitHub / GitLab / Bitbucket repos (forge)

The `forge` commands pull repo lists from your configured servers and can
clone missing repos to a workspace. These are useful for keeping a multi-host
workspace in sync.

### List repos from a server
```bash
lantern forge list --server github.com --output data/github.json
lantern forge list --server gitlab.com --output data/gitlab.json
lantern forge list --server bitbucket.org --output data/bitbucket.json
```

### Clone missing repos to a workspace
```bash
lantern forge clone --server github.com --input data/github.json --root ~/workspace
lantern forge clone --server gitlab.com --input data/gitlab.json --root ~/workspace
lantern forge clone --server bitbucket.org --input data/bitbucket.json --root ~/workspace
```

### Check status or pull after cloning
```bash
lantern status --root ~/workspace --fetch
lantern sync --root ~/workspace --pull --only-clean --only-upstream
```

## Gists and snippets

Lantern supports:
- GitHub gists (via `lantern forge gists`).
- GitHub/GitLab/Bitbucket snippets (via `lantern forge snippets`).

### List gists (GitHub)
```bash
lantern forge gists list --server github.com --output data/gists.json
```

### Download gist files (GitHub)
```bash
lantern forge gists clone GIST_ID --input data/gists.json --output-dir ./gists
lantern forge gists clone GIST_ID --file readme.md --output-dir ./gists
```

### Update a gist (GitHub)
```bash
lantern forge gists update GIST_ID --file ./notes.txt --force
lantern forge gists update GIST_ID --file readme.md=./README.md
lantern forge gists update GIST_ID --delete old.txt --force
```

### Create a gist (GitHub)
```bash
lantern forge gists create --file ./notes.txt --description "Notes" --public
lantern forge gists create --file ./notes.txt --description "Notes" --private
```

### List snippets (GitHub/GitLab/Bitbucket)
```bash
lantern forge snippets list --server github.com --output data/snippets.json
lantern forge snippets list --server gitlab.com --output data/snippets.json
lantern forge snippets list --server bitbucket.org --output data/snippets.json
```

### Download snippet files (GitHub/GitLab/Bitbucket)
```bash
lantern forge snippets clone SNIPPET_ID --input data/snippets.json --output-dir ./snippets
lantern forge snippets clone SNIPPET_ID --file README.md --output-dir ./snippets
```

### Update or create a snippet (GitHub only)
```bash
lantern forge snippets update SNIPPET_ID --file ./notes.txt --force
lantern forge snippets update SNIPPET_ID --delete old.txt --force
lantern forge snippets create --file ./notes.txt --description "Notes" --public
lantern forge snippets create --file ./notes.txt --description "Notes" --private
```

GitLab/Bitbucket: list and clone only (create/update not yet implemented).
