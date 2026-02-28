# Changelog

All notable changes to git-lantern are documented in this file.

## [Unreleased]

## [0.2.0] - 2026-02-28

### Added

- Added GitHub organization-aware repository listing:
  - `lantern forge list --org <name>` (repeatable),
  - `lantern forge list --all-orgs`,
  - `lantern forge list --with-user` to combine personal repos with selected org repos.
- Added the same org selection flags to `lantern fleet plan` / `lantern fleet apply` when remote repos are fetched from the configured server.
- Added support for per-organization token overrides in server config using `organizations` / `orgs` entries.

### Changed

- GitHub forge/fleet repo listings now emit namespaced repo names (`owner/repo`) to keep multi-organization results unambiguous.
- Documentation now includes examples for multi-organization selection and per-org token configuration under a single Git service.

### Added

- `fleet` workflow improvements:
  - `lantern fleet plan --with-prs` now includes fresh open PR numbers and latest active PR branch hints (GitHub).
  - `lantern fleet apply --checkout-pr <number>` to checkout/update the branch behind a PR number.
  - `lantern fleet apply --checkout-branch <name>` to checkout/update arbitrary branches (including PR branches).
- TUI `fleet` now includes `Smart Sync` preset flow with minimal steps:
  - presets: `Fast Pull`, `Branch Rollout`, `PR Rollout`, `Full Reconcile`,
  - scope selector: all actionable, only clean, or selected repos,
  - full preflight summary of planned actions before execution,
  - mandatory per-repo checklist confirmation before execution,
  - explicit push-mode selection (skip push or push ahead repos),
  - runs through existing `fleet plan/apply` behavior.
- `lantern fleet apply --log-json <path>` now writes a full execution report (actions per repo, branch updates, summary totals).
- `lantern fleet logs --latest` added for quick inspection of fleet execution logs; by default it renders full JSON in pretty format via `jq` (with fallback output when `jq` is unavailable).
- TUI `fleet` apply flow now:
  - loads repo context first,
  - shows latest branch + PR numbers before selection,
  - supports informed mode choice (sync, checkout PR, checkout branch).
- `lazygit` integration:
  - new `lantern lazygit` command with `--repo`, `--path`, and `--select`.
  - direct TUI `lazygit` menu action.
- Persisted TUI workspace settings:
  - `workspace_root` persisted and reused between sessions.
  - `scan_json_path` persisted and reused by `scan`, `table`, and `report`.
  - global CLI override for TUI root: `lantern --tui --tui-root <path>` (or `lantern --tui-root <path> --tui`).

### Changed

- TUI now prioritizes `fleet` as the primary multi-repo flow.
- Repository-oriented displays are consistently sorted alphabetically by repo name in CLI/TUI outputs.
- TUI `forge -> clone` now auto-resolves repository-list JSON and auto-generates it via `forge list` when missing.
- TUI `table`/`report` no longer prompt for JSON scan path each time; they use configured `scan_json_path`.
- TUI `scan` writes directly to configured `scan_json_path` without repeated path prompts.
- `clean`/`only-clean` sync gating now ignores local uncommitted/untracked files and only treats in-progress Git operations (merge/rebase/cherry-pick/etc.) as non-clean.
- `sync` now records unsuccessful repo actions with rollback attempts and writes a JSON issue log under `data/sync-logs/`.
- `fleet apply` now reports unsuccessful operations in CLI output and includes failure/rollback details in fleet JSON logs.

### Fixed

- Fixed `forge clone --tui` behavior where global `--tui` could intercept subcommand-specific TUI flow.
- Fixed `fleet` TUI passing `--with-prs` to `fleet apply` (invalid argument).
- Fixed `fleet` PR enrichment failures (`404`/network errors) from aborting the whole operation; per-repo PR errors are now non-fatal.
- Added progress indicators for long operations:
  - `status --fetch`, `scan --fetch`, `sync`, and `fleet plan/apply` in CLI.
  - visible working indicators for long `status`, `scan`, and `fleet` operations in TUI.

### Added - TUI Features

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
  - `fleet` - Unified fleet plan/apply for clone/pull/push, with sync-only apply mode and dry-run/only-clean controls
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
