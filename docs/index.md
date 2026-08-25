# Git Lantern

**See the state of every local repository and the Git servers behind them.**

Git Lantern is a Python command-line tool for scanning a workspace, seeing which repositories need attention, and applying carefully scoped Git actions. It works locally and with GitHub, GitLab, and Bitbucket through configurable server connections.

## Start here

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
lantern --help
```

The default `lantern` command opens an interactive terminal UI when `dialog` is installed. Every workflow is also available as a scriptable subcommand.

## What it helps with

- Build a fleet overview of local repositories, including dirty worktrees, branch divergence, remotes, and pull-request context.
- Plan and apply selected synchronization actions such as cloning missing repositories, pulling clean behind branches, pushing ahead branches, and switching to the newest branch.
- Scan, filter, find, report on, and detect duplicate local repositories.
- List repositories on GitHub, GitLab, or Bitbucket; clone a saved list; and manage gists and snippets where supported.
- Configure servers interactively or import/export their configuration.
- Find open pull requests with unresolved review threads using `lantern pr sweep`.

## Recommended first workflow

```bash
# Inspect a workspace and save a reusable snapshot.
lantern fleet overview --root ~/workspace --server github.com --fetch \
  --with-prs --output data/fleet-snapshot.json

# Review the proposed actions before changing repositories.
lantern fleet plan --root ~/workspace --server github.com --fetch

# Apply only the actions you explicitly choose.
lantern fleet apply --root ~/workspace --snapshot data/fleet-snapshot.json \
  --pull-behind --only-clean
```

Read the [use cases](use-cases.md) for complete workflows and the [command reference](commands.md) for arguments, output, and safety behavior.

## Links

- [Source repository](https://github.com/nikolareljin/git-lantern)
