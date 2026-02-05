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

- **Session-based root directory**: Set the workspace root once via "Session settings" and it persists throughout the TUI session. No more repeated prompts for the root directory on every operation.

- **Full menu-driven interface** with the following operations:
  - `servers` - View configured Git servers
  - `config` - Server configuration (setup, export, import, path)
  - `settings` - Session settings (change root directory)
  - `repos` - List local repositories
  - `status` - Show repository status with branch divergence
  - `scan` - Scan repositories and save to JSON
  - `table` - Render table from a JSON scan file
  - `sync` - Sync repositories (fetch/pull/push) with dry-run option
  - `find` - Find repositories by name or remote URL
  - `duplicates` - Find duplicate repository clones
  - `forge` - Git forge operations (list repos, list to file, clone)

- **Scrollable output**: All table outputs are displayed in scrollable dialog textboxes for easy viewing of large repository lists.

- **Clean exit**: Screen is cleared when exiting TUI mode.

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
