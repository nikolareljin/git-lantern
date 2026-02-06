# Changelog

All notable changes to git-lantern are documented in this file.

## [Unreleased]

### Added

#### Interactive TUI Mode (`lantern --tui` / `lantern -t`)

A new interactive terminal user interface powered by the `dialog` CLI tool. Launch with:

```bash
lantern --tui
lantern -t
```

**Features:**

- **Session settings**: Configure root directory, max scan depth, include hidden directories, and include forks - all persist throughout the TUI session. Settings are shown in the main menu header.

- **Full menu-driven interface** with the following operations:
  - `servers` - View configured Git servers
  - `config` - Server configuration (setup, export, import, path)
  - `settings` - Session settings (root, depth, hidden, forks)
  - `repos` - List local repositories
  - `status` - Show repository status with branch divergence
  - `scan` - Scan repositories and save to JSON
  - `table` - Render table from a JSON scan file
  - `sync` - Sync repositories (fetch/pull/push) with dry-run, only-clean, only-upstream options
  - `find` - Find repositories by name or remote URL
  - `duplicates` - Find duplicate repository clones
  - `forge` - Git forge operations (list/clone repos, list/download/create gists and snippets)
  - `report` - Export scan results to CSV, JSON, or Markdown

- **Gists and snippets in TUI**: The forge submenu now includes:
  - List gists/snippets (display or save to file)
  - Download a specific gist/snippet by ID
  - Create a new gist (GitHub only)

- **Report export in TUI**: New "report" menu option to export scan results with format selection, column filtering, and output path.

- **Advanced sync options**: TUI sync now prompts for `--only-clean` (skip dirty repos) and `--only-upstream` (skip repos without upstream), and passes session depth/hidden settings.

- **Scrollable output**: All table outputs are displayed in scrollable dialog textboxes for easy viewing of large repository lists.

- **Clean exit**: Screen is cleared when exiting TUI mode and between operations.

#### Interactive Server Configuration (`lantern config setup`)

A new TUI-based server configuration wizard:

```bash
lantern config setup
```

**Features:**

- **Server presets** for common providers:
  - github.com (GitHub)
  - gitlab.com (GitLab)
  - bitbucket.org (Bitbucket)

- **Custom server support**: Add self-hosted GitHub Enterprise, GitLab, or Bitbucket servers with custom API base URLs.

- **Full server management**:
  - Add new servers (from presets or custom)
  - Edit existing servers (username, token)
  - Remove servers
  - Set the default server

- **Secure token handling**: Tokens are entered via password input (hidden) and saved with restricted file permissions (0600).

- **Non-destructive workflow**: Changes are held in memory until you explicitly choose "Save and exit".

### Changed

- **Forge list in TUI**: Split into two separate menu options:
  - "List remote repositories (display)" - Shows results directly in the TUI
  - "List remote repositories (save to file)" - Prompts for output file path
- **Forge list**: Now respects session "include forks" setting.

### Fixed

- **PYTHONPATH hardcoding**: Subprocess calls now use a dynamically resolved `_SRC_DIR` instead of hardcoded `"src"`, fixing breakage in pip-installed and system-packaged deployments.
- **Subprocess error handling**: All subprocess calls now display stderr in an error dialog on failure instead of silently suppressing errors.
- **Session root validation**: Consolidated 6 duplicate root directory validation blocks into a single `_validate_session_root` helper.
- **Screen clearing**: Consistent screen clearing between all menu operations.

### Dependencies

The TUI features require the `dialog` CLI tool:

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

---

## Previous Releases

See git history for changes prior to this changelog.
