"""lantern hooks: which clones, which setup-hooks.sh calls, export and import.

A stand-in setup-hooks.sh keeps each clone's state in .git/fake-hooks and logs
every call, so the tests see exactly what lantern asks script-helpers to do.
One test runs the real script-helpers (0.50.0+) when it is available.
"""
import argparse
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from lantern import cli
from lantern import hooks

FAKE_SETUP = r'''#!/usr/bin/env bash
state="$(git rev-parse --git-dir)/fake-hooks"
mode=none; taken=no; agents=""; claude=no; unscanned=no
[[ -f "$state" ]] && source "$state"
echo "$(basename "$(pwd)") $*" >> "$FAKE_LOG"
[[ -f "$(git rev-parse --git-dir)/fail-status" && "$*" == "--status --porcelain" ]] && { echo "boom"; exit 1; }
case "$*" in
  "--status --porcelain") printf 'mode=%s\ttaken_over=%s\tclaude_repo=%s\tagents=%s\tunscanned=%s\n' "$mode" "$taken" "$claude" "$agents" "$unscanned"; exit 0 ;;
  "") mode=chained; taken=no ;;
  "--repair") taken=no; unscanned=no ;;
  "--uninstall") mode=none ;;
  "--claude-repo") claude=yes ;;
  "--claude-repo --uninstall") claude=no ;;
  --agent-repo*--uninstall) agents="" ;;
  --agent-repo*) set -- $*; agents="${agents:+$agents,}$2" ;;
  "--fail") exit 1 ;;
esac
printf 'mode=%s\ntaken=%s\nagents=%s\nclaude=%s\nunscanned=%s\n' "$mode" "$taken" "$agents" "$claude" "$unscanned" > "$state"
'''


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class HooksTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = self.tmp.name
        self.home = os.path.join(base, "sh")
        os.makedirs(os.path.join(self.home, "scripts"))
        with open(os.path.join(self.home, "VERSION"), "w") as fh:
            fh.write("0.50.0\n")
        with open(os.path.join(self.home, "scripts", "setup-hooks.sh"), "w", newline="\n") as fh:
            fh.write(FAKE_SETUP)
        self.log = os.path.join(base, "calls.log")
        open(self.log, "w").close()
        gitconfig = os.path.join(base, "gitconfig")
        open(gitconfig, "w").close()
        env = {"SCRIPT_HELPERS_HOME": self.home, "SCRIPT_HELPERS_OWNERS": "me,my-llc",
               "FAKE_LOG": self.log, "GIT_CONFIG_GLOBAL": gitconfig, "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = os.path.join(base, "tree")
        os.makedirs(self.root)

    def clone(self, name, origin, root=None):
        path = os.path.join(root or self.root, name)
        git("init", "-q", path)
        if origin:
            git("remote", "add", "origin", origin, cwd=path)
        return path

    def calls(self):
        with open(self.log) as fh:
            return [line.strip() for line in fh if "--status --porcelain" not in line]

    def run_cmd(self, *argv):
        parser_args = ["hooks", *argv]
        if not any(a.startswith("--root") for a in argv):
            parser_args += ["--root", self.root]
        args = self.parser().parse_args(parser_args)
        return args.func(args)

    @staticmethod
    def parser():
        captured = {}
        real = argparse.ArgumentParser.parse_args

        def grab(self_parser, *a, **k):
            captured["p"] = self_parser
            raise SystemExit(0)
        with mock.patch.object(argparse.ArgumentParser, "parse_args", grab), mock.patch("sys.argv", ["lantern"]):
            try:
                cli.main()
            except SystemExit:
                pass
        parser = captured["p"]
        parser.parse_args = real.__get__(parser)
        return parser


class OwnershipAndApply(HooksTestBase):
    def test_slug_parsing(self):
        for url in ("git@github.com:Me/Repo.git", "https://github.com/me/repo", "ssh://git@github.com/me/repo.git",
                    "https://token@github.com/me/repo/", "ssh://git@github.com:22/me/repo"):
            self.assertEqual(hooks.github_slug(url), ("me", "repo"), url)
        for url in ("ssh://git@ssh.github.com:443/me/repo", "https://www.github.com/me/repo"):
            self.assertEqual(hooks.github_slug(url), ("me", "repo"), url)
        for url in ("git@gitlab.com:me/repo.git", "https://github.com.evil.example/me/repo",
                    "https://evilgithub.com/me/repo", "https://www.github.com.evil/me/repo", "", None):
            self.assertIsNone(hooks.github_slug(url), url)

    def test_apply_touches_only_owned_clones(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.clone("llc", "https://github.com/my-llc/llc.git")
        self.clone("employer", "git@github.com:acme/employer.git")
        self.clone("elsewhere", "git@gitlab.com:me/elsewhere.git")
        self.clone("noremote", "")
        self.assertEqual(self.run_cmd("apply"), 0)
        self.assertEqual(sorted(self.calls()), ["llc", "mine"])

    def test_dry_run_changes_nothing(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.assertEqual(self.run_cmd("apply", "--dry-run", "--agents", "all"), 0)
        self.assertEqual(self.calls(), [])

    def test_apply_is_idempotent_and_adds_agents_once(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply", "--agents", "claude,codex")
        self.assertEqual(self.calls(), ["mine", "mine --claude-repo", "mine --agent-repo codex"])
        open(self.log, "w").close()
        self.run_cmd("apply", "--agents", "claude,codex")
        self.assertEqual(self.calls(), [])

    def test_taken_over_is_repaired(self):
        path = self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply")
        with open(os.path.join(path, ".git", "fake-hooks"), "a") as fh:
            fh.write("taken=yes\n")
        open(self.log, "w").close()
        self.assertEqual(self.run_cmd("repair"), 0)
        self.assertEqual(self.calls(), ["mine --repair"])

    def test_unscanned_direct_setup_is_repaired_by_repair_and_apply(self):
        path = self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply")
        with open(os.path.join(path, ".git", "fake-hooks"), "a") as fh:
            fh.write("unscanned=yes\n")
        with mock.patch("sys.stdout") as out:
            self.run_cmd("status")
        self.assertIn("NOT SCANNED", "".join(c.args[0] for c in out.write.call_args_list))
        open(self.log, "w").close()
        self.assertEqual(self.run_cmd("repair"), 0)
        self.assertEqual(self.calls(), ["mine --repair"])
        with open(os.path.join(path, ".git", "fake-hooks"), "a") as fh:
            fh.write("unscanned=yes\n")
        open(self.log, "w").close()
        self.assertEqual(self.run_cmd("apply"), 0)
        self.assertEqual(self.calls(), ["mine --repair"])

    def test_batch_limits_the_run(self):
        for n in ("a", "b", "c"):
            self.clone(n, "git@github.com:me/{}.git".format(n))
        self.run_cmd("apply", "--batch", "2")
        self.assertEqual(len(self.calls()), 2)

    def test_remove(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply", "--agents", "codex")
        open(self.log, "w").close()
        self.run_cmd("remove", "--agents", "codex")
        self.assertEqual(self.calls(), ["mine --agent-repo codex --uninstall"])
        open(self.log, "w").close()
        self.run_cmd("remove")
        self.assertEqual(self.calls(), ["mine --uninstall"])

    def test_remove_without_agents_removes_everything_ours(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply", "--agents", "claude,codex")
        open(self.log, "w").close()
        self.run_cmd("remove")
        self.assertEqual(self.calls(), ["mine --claude-repo --uninstall", "mine --agent-repo codex --uninstall", "mine --uninstall"])

    def test_a_configured_home_that_is_unusable_is_refused_not_skipped(self):
        self.clone("mine", "git@github.com:me/mine.git")
        with mock.patch.dict(os.environ, {"SCRIPT_HELPERS_HOME": os.path.join(self.tmp.name, "nowhere")}):
            self.assertEqual(self.run_cmd("status"), 2)

    def test_a_failed_status_is_a_failure_never_an_install(self):
        path = self.clone("mine", "git@github.com:me/mine.git")
        open(os.path.join(path, ".git", "fail-status"), "w").close()
        self.assertEqual(self.run_cmd("apply"), 1)
        self.assertEqual(self.calls(), [])                  # nothing installed
        self.assertEqual(self.run_cmd("status"), 1)
        self.assertEqual(self.run_cmd("remove"), 1)
        self.assertEqual(self.run_cmd("export", os.path.join(self.tmp.name, "x.json")), 2)

    def test_a_failure_is_exit_1(self):
        self.clone("mine", "git@github.com:me/mine.git")
        with mock.patch.object(hooks, "plan_apply", return_value=[["--fail"]]):
            self.assertEqual(self.run_cmd("apply"), 1)

    def test_configuration_errors_are_exit_2(self):
        self.clone("mine", "git@github.com:me/mine.git")
        with mock.patch.dict(os.environ, {"SCRIPT_HELPERS_OWNERS": ""}):
            self.assertEqual(self.run_cmd("status"), 2)
        with open(os.path.join(self.home, "VERSION"), "w") as fh:
            fh.write("0.49.0\n")
        with mock.patch.object(hooks, "__file__", os.path.join(self.tmp.name, "nowhere", "a", "hooks.py")):
            self.assertEqual(self.run_cmd("status"), 2)
        self.assertEqual(self.run_cmd("apply", "--agents", "nope"), 2)


class ExportImport(HooksTestBase):
    def test_round_trip_matches_by_remote_not_path(self):
        self.clone("mine", "git@github.com:me/mine.git")
        self.clone("llc", "https://github.com/my-llc/llc.git")
        self.clone("employer", "git@github.com:acme/employer.git")
        self.run_cmd("apply", "--agents", "claude")
        out = os.path.join(self.tmp.name, "hooks.json")
        self.assertEqual(self.run_cmd("export", out), 0)
        data = json.load(open(out))
        self.assertEqual(sorted(r["remote"] for r in data["repos"]), ["github.com/me/mine", "github.com/my-llc/llc"])
        self.assertNotIn(self.tmp.name, open(out).read())          # no local paths in the export
        # Another machine: other paths, other URL forms, one clone missing.
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        self.clone("renamed-mine", "https://github.com/me/mine", root=other)
        open(self.log, "w").close()
        self.assertEqual(self.run_cmd("import", out, "--root", other), 0)
        self.assertEqual(self.calls(), ["renamed-mine", "renamed-mine --claude-repo"])

    def test_two_clones_of_one_repository(self):
        self.clone("one", "git@github.com:me/mine.git")
        self.clone("two", "https://github.com/me/mine")
        self.run_cmd("apply", "--agents", "codex", "--only", "one")
        self.run_cmd("apply", "--agents", "claude", "--only", "two")
        out = os.path.join(self.tmp.name, "hooks.json")
        self.run_cmd("export", out)
        repos = json.load(open(out))["repos"]
        self.assertEqual(len(repos), 1)                              # one entry per repository
        self.assertEqual(sorted(repos[0]["agents"]), ["claude", "codex"])
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        self.clone("a", "git@github.com:me/mine.git", root=other)
        self.clone("b", "https://github.com/me/mine.git", root=other)
        open(self.log, "w").close()
        self.run_cmd("import", out, "--root", other)
        self.assertEqual(sorted(c.split()[0] for c in self.calls()), ["a", "a", "a", "b", "b", "b"])   # both clones

    def test_unreadable_or_unwritable_files_are_exit_2_not_a_traceback(self):
        self.clone("mine", "git@github.com:me/mine.git")
        notjson = os.path.join(self.tmp.name, "notjson")
        with open(notjson, "w") as fh:
            fh.write("not json")
        self.assertEqual(self.run_cmd("import", notjson), 2)
        self.assertEqual(self.run_cmd("import", os.path.join(self.tmp.name, "missing.json")), 2)
        self.assertEqual(self.run_cmd("export", os.path.join(self.tmp.name, "no", "dir", "x.json")), 2)
        self.assertEqual(self.run_cmd("status", "--root", os.path.join(self.tmp.name, "nope")), 2)

    def test_bad_export_is_exit_2(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as fh:
            json.dump({"kind": "something-else"}, fh)
        self.assertEqual(self.run_cmd("import", bad), 2)


class FleetApplyRepairs(HooksTestBase):
    def test_fleet_apply_repairs_taken_over_clones(self):
        path = self.clone("mine", "git@github.com:me/mine.git")
        self.run_cmd("apply")
        with open(os.path.join(path, ".git", "fake-hooks"), "a") as fh:
            fh.write("taken=yes\n")
        open(self.log, "w").close()
        args = argparse.Namespace(root=self.root, max_depth=6, include_hidden=False, dry_run=False, no_hooks_repair=False)
        with mock.patch.object(cli, "cmd_fleet_apply", return_value=0):
            self.assertEqual(cli.cmd_fleet_apply_and_repair(args), 0)
        self.assertEqual(self.calls(), ["mine --repair"])
        with open(os.path.join(path, ".git", "fake-hooks"), "a") as fh:
            fh.write("taken=yes\n")
        open(self.log, "w").close()
        args.no_hooks_repair = True
        with mock.patch.object(cli, "cmd_fleet_apply", return_value=0):
            cli.cmd_fleet_apply_and_repair(args)
        self.assertEqual(self.calls(), [])

    def test_fleet_apply_repairs_only_its_repos_and_keeps_stdout(self):
        for n in ("a", "b"):
            p = self.clone(n, "git@github.com:me/{}.git".format(n))
            self.run_cmd("apply", "--only", n)
            with open(os.path.join(p, ".git", "fake-hooks"), "a") as fh:
                fh.write("taken=yes\n")
        open(self.log, "w").close()
        args = argparse.Namespace(root=self.root, max_depth=6, include_hidden=False, dry_run=False,
                                  no_hooks_repair=False, repos="a")
        import io
        out = io.StringIO()
        with mock.patch.object(cli, "cmd_fleet_apply", return_value=0), mock.patch("sys.stdout", out):
            cli.cmd_fleet_apply_and_repair(args)
        self.assertEqual(self.calls(), ["a --repair"])
        self.assertEqual(out.getvalue(), "")

    def test_fleet_apply_without_owners_is_unchanged(self):
        args = argparse.Namespace(root=self.root, max_depth=6, include_hidden=False, dry_run=False, no_hooks_repair=False)
        with mock.patch.dict(os.environ, {"SCRIPT_HELPERS_OWNERS": ""}), \
                mock.patch.object(cli, "cmd_fleet_apply", return_value=3):
            self.assertEqual(cli.cmd_fleet_apply_and_repair(args), 3)


def _real_script_helpers():
    for root in (os.environ.get("LANTERN_TEST_SCRIPT_HELPERS", ""),
                 os.path.join(os.path.dirname(__file__), "..", "scripts", "script-helpers")):
        try:
            if root and hooks._version_tuple(open(os.path.join(root, "VERSION")).read()) >= hooks.MIN_VERSION:
                return os.path.abspath(root)
        except OSError:
            continue
    return ""


@unittest.skipUnless(_real_script_helpers(), "script-helpers 0.50.0+ not available (vendored copy older)")
class RealScriptHelpers(HooksTestBase):
    def test_apply_then_remove_restores_an_existing_hooks_path(self):
        os.environ["SCRIPT_HELPERS_HOME"] = _real_script_helpers()
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ["LOCALAPPDATA"] = os.environ["XDG_DATA_HOME"]
        path = self.clone("mine", "git@github.com:me/mine.git")
        # A native absolute path: on Windows a POSIX one (/opt/...) is rewritten
        # by Git Bash on its way to git.exe (script-helpers issue 177).
        employer = os.path.join(self.tmp.name, "employer-hooks").replace(os.sep, "/")
        git("config", "core.hooksPath", employer, cwd=path)
        self.assertEqual(self.run_cmd("apply"), 0)
        state = hooks.repo_state(hooks.find_setup_script(), path)
        self.assertEqual((state.get("mode"), state.get("previous")), ("chained", employer))
        self.assertEqual(self.run_cmd("remove"), 0)
        got = subprocess.run(["git", "-C", path, "config", "core.hooksPath"], stdout=subprocess.PIPE, text=True).stdout.strip()
        self.assertEqual(got, employer)


class InputChecks(HooksTestBase):
    def write_export(self, repos, version=1):
        path = os.path.join(self.tmp.name, "x.json")
        with open(path, "w") as fh:
            json.dump({"kind": "lantern-hooks", "version": version, "repos": repos}, fh)
        return path

    def test_negative_batch_is_refused(self):
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr"):
            self.run_cmd("apply", "--batch", "-1")
        self.assertEqual(ctx.exception.code, 2)

    def test_import_refuses_malformed_entries_before_touching_clones(self):
        self.clone("sub", "git@github.com:me/sub.git")
        bad = [{"remote": "github.com/me/sub", "git_hooks": "no", "agents": []},
               {"remote": "github.com/me/sub", "git_hooks": True, "agents": "codex"},
               {"remote": "github.com/me/sub", "git_hooks": True, "agents": 5},
               {"remote": "github.com/me/sub", "git_hooks": True, "agents": ["--global"]},
               {"remote": "", "git_hooks": True, "agents": []}]
        with mock.patch("sys.stderr"):
            for entry in bad:
                self.assertEqual(self.run_cmd("import", self.write_export([entry])), 2, entry)
            self.assertEqual(self.run_cmd("import", self.write_export([], version=True)), 2)
        self.assertEqual(self.calls(), [])

    def test_import_matches_an_unnormalized_remote(self):
        self.clone("sub", "git@github.com:me/sub.git")
        entry = {"remote": "git@github.com:Me/Sub.git", "git_hooks": True, "agents": []}
        self.assertEqual(self.run_cmd("import", self.write_export([entry])), 0)
        self.assertEqual(self.calls(), ["sub"])

    def test_linked_worktree_is_left_to_its_main_clone(self):
        main = self.clone("a", "git@github.com:me/a.git")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "x", cwd=main)
        git("worktree", "add", "-q", os.path.join(self.root, "a-wt"), cwd=main)
        self.assertEqual(self.run_cmd("apply", "--only", "a-wt"), 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_cmd("apply"), 0)
        self.assertEqual(self.calls(), ["a"])


class OnlySelection(HooksTestBase):
    def setUp(self):
        super().setUp()
        self.a = self.clone("utils", "git@github.com:me/utils.git", root=os.path.join(self.root, "tools"))
        self.b = self.clone("utils", "git@github.com:my-llc/utils.git", root=os.path.join(self.root, "clients", "x"))
        self.c = self.clone("solo", "git@github.com:me/solo.git")

    def test_unique_name_selects_that_clone(self):
        self.assertEqual(self.run_cmd("apply", "--only", "SOLO"), 0)
        self.assertEqual(self.calls(), ["solo"])

    def test_shared_name_is_refused(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(self.run_cmd("apply", "--only", "utils"), 2)
        self.assertEqual(self.calls(), [])

    def test_unknown_item_is_refused(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(self.run_cmd("apply", "--only", "solo,nope"), 2)
        self.assertEqual(self.calls(), [])

    def test_paths_relative_to_root_cwd_and_absolute(self):
        cwd = os.getcwd()
        os.chdir(os.path.join(self.root, "tools"))
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(self.run_cmd("apply", "--only", "./utils"), 0)       # cwd-relative: tools/utils
        self.assertEqual(self.calls(), ["utils"])
        open(self.log, "w").close()
        self.assertEqual(self.run_cmd("apply", "--only", self.a), 0)          # absolute: already set up
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_cmd("apply", "--only", "clients/x/utils"), 0)   # --root-relative
        self.assertEqual(self.calls(), ["utils"])


class WindowsBash(unittest.TestCase):
    def setUp(self):
        hooks._BASH = None
        self.addCleanup(setattr, hooks, "_BASH", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def nt(self, env=None, git=None, exec_path=""):
        env = dict(env or {})
        if git:
            os.makedirs(os.path.dirname(git), exist_ok=True)
            open(git, "w").close()
            env.setdefault("PATH", os.path.dirname(git))
        return [mock.patch.object(hooks.os, "name", "nt"),
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(hooks, "_git", return_value=exec_path)]

    def run_nt(self, **kw):
        patches = self.nt(**kw)
        for p in patches:
            p.start()
        try:
            return hooks.find_bash()
        finally:
            for p in reversed(patches):
                p.stop()

    def make(self, *parts):
        path = os.path.join(self.tmp.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_override_wins(self):
        bash = self.make("custom", "bash.exe")
        self.make("Git", "bin", "bash.exe")
        self.assertEqual(self.run_nt(env={"CLAUDE_CODE_GIT_BASH_PATH": bash},
                                     git=os.path.join(self.tmp.name, "Git", "cmd", "git.exe")), bash)

    def test_bash_next_to_git_exe(self):
        bash = self.make("Git", "bin", "bash.exe")
        self.assertEqual(self.run_nt(git=os.path.join(self.tmp.name, "Git", "cmd", "git.exe")), bash)

    def test_bash_from_exec_path(self):
        bash = self.make("Git", "bin", "bash.exe")
        exec_path = os.path.join(self.tmp.name, "Git", "mingw64", "libexec", "git-core")
        self.assertEqual(self.run_nt(git=os.path.join(self.tmp.name, "elsewhere", "git.exe"), exec_path=exec_path), bash)

    def test_standard_install_folder(self):
        bash = self.make("LocalAppData", "Programs", "Git", "bin", "bash.exe")
        self.assertEqual(self.run_nt(env={"LOCALAPPDATA": os.path.join(self.tmp.name, "LocalAppData")}), bash)

    def test_git_in_a_relative_path_entry_is_ignored(self):
        planted = self.make("clone", "bin", "bash.exe")
        git = os.path.join(self.tmp.name, "clone", "cmd", "git.exe")
        open(self.make("clone", "cmd", "git.exe"), "w").close()
        cwd = os.getcwd()
        os.chdir(os.path.join(self.tmp.name, "clone"))
        self.addCleanup(os.chdir, cwd)
        with self.assertRaises(hooks.HooksError):
            self.run_nt(env={"PATH": os.pathsep.join(["cmd", "."])})
        self.assertTrue(os.path.isfile(planted) and os.path.isfile(git))

    def test_no_git_bash_is_refused(self):
        with self.assertRaises(hooks.HooksError):
            self.run_nt()

    def test_missing_bash_is_a_configuration_error(self):
        with mock.patch.object(hooks, "find_bash", return_value=os.path.join(self.tmp.name, "no-bash")):
            with self.assertRaises(hooks.HooksError):
                hooks.run_setup("x.sh", self.tmp.name, [])


if __name__ == "__main__":
    unittest.main()
