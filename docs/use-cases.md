# Lantern use cases

This guide focuses on real workflows. For the full command reference, see
`docs/commands.md`.

## Interactive TUI Mode

Lantern provides a full interactive TUI (terminal user interface) powered by the `dialog` CLI. This is ideal for users who prefer menu-driven interfaces over remembering command-line flags.

### Launching TUI Mode

```bash
lantern --tui
lantern -t
```

### TUI Workflow: First-Time Setup

1. **Launch TUI**: `lantern --tui`
2. **Configure servers**: Select `config` > `setup`
3. **Add a server**: Choose from presets (github.com, gitlab.com, bitbucket.org) or add a custom server
4. **Enter credentials**: Provide your username and optionally your API token
5. **Save**: Select "Save and exit" to write the configuration
6. **Set root directory**: Go to `settings` > "Change root directory" and enter your workspace path (e.g., `~/workspace`)

Now all repository operations will use your configured root directory automatically.

### TUI Workflow: Daily Usage

Once configured, a typical session looks like:

1. **Launch TUI**: `lantern --tui`
2. **Check status**: Select `status` to see all repos with branch divergence info
3. **Sync repos**: Select `sync` > "Fetch and pull" to update all clean repos
4. **Find a repo**: Select `find` and enter a name filter to locate a specific repo
5. **Exit**: Select `exit` (screen is cleared automatically)

### TUI Workflow: Cloning from a Git Server

1. **Launch TUI**: `lantern --tui`
2. **List remote repos**: Select `forge` > "List remote repositories (save to file)"
3. **Choose server**: Select your configured server (e.g., github.com)
4. **Save the list**: Enter a path like `data/github.json`
5. **Clone repos**: Select `forge` > "Clone repositories from list"
6. **Select the JSON**: Enter `data/github.json`
7. **Choose clone directory**: Defaults to your session root
8. **Select repos**: Use the checklist to pick which repos to clone

### TUI Workflow: Interactive Server Setup

The `lantern config setup` command (also accessible via TUI `config` > `setup`) provides a wizard for server management:

```
Server Configuration
├── Add a new server
│   ├── github.com (preset)
│   ├── gitlab.com (preset)
│   ├── bitbucket.org (preset)
│   └── custom (enter hostname, provider, base URL)
├── Edit existing server (change username/token)
├── Remove a server
├── Set default server
├── Save and exit
└── Exit without saving
```

**Adding a custom self-hosted server:**

1. Select "Add a new server" > "custom"
2. Enter hostname (e.g., `gitlab.mycompany.com`)
3. Select provider type (github/gitlab/bitbucket)
4. Enter API base URL (e.g., `https://gitlab.mycompany.com/api/v4`)
5. Enter your username
6. Optionally add your API token (entered securely, not echoed)

### TUI Features

- **Session-based root directory**: Set once, used for all operations in the session
- **Scrollable output**: Large tables are displayed in scrollable text boxes
- **Clean exit**: Screen is cleared when exiting
- **Non-destructive config editing**: Server changes are only saved when you explicitly choose "Save and exit"
- **Secure token entry**: Tokens are entered via password fields (not visible on screen)

### Installing dialog

The TUI requires the `dialog` CLI:

```bash
# Debian/Ubuntu
sudo apt install dialog

# macOS
brew install dialog

# Fedora/RHEL
sudo dnf install dialog

# Arch Linux
sudo pacman -S dialog
```

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
Omit `--output` to render a table instead of JSON.
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
Omit `--output` to render a table instead of JSON.
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
Omit `--output` to render a table instead of JSON.
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
