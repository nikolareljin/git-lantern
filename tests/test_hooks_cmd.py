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
mode=none; taken=no; agents=""; claude=no
[[ -f "$state" ]] && source "$state"
echo "$(basename "$(pwd)") $*" >> "$FAKE_LOG"
case "$*" in
  "--status --porcelain") printf 'mode=%s\ttaken_over=%s\tclaude_repo=%s\tagents=%s\n' "$mode" "$taken" "$claude" "$agents"; exit 0 ;;
  "") mode=chained; taken=no ;;
  "--repair") taken=no ;;
  "--uninstall") mode=none ;;
  "--claude-repo") claude=yes ;;
  "--claude-repo --uninstall") claude=no ;;
  --agent-repo*--uninstall) agents="" ;;
  --agent-repo*) set -- $*; agents="${agents:+$agents,}$2" ;;
  "--fail") exit 1 ;;
esac
printf 'mode=%s\ntaken=%s\nagents=%s\nclaude=%s\n' "$mode" "$taken" "$agents" "$claude" > "$state"
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
        with open(os.path.join(self.home, "scripts", "setup-hooks.sh"), "w") as fh:
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
        for url in ("git@gitlab.com:me/repo.git", "https://github.com.evil.example/me/repo", "", None):
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
        git("config", "core.hooksPath", "/opt/employer-hooks", cwd=path)
        self.assertEqual(self.run_cmd("apply"), 0)
        state = hooks.repo_state(hooks.find_setup_script(), path)
        self.assertEqual((state.get("mode"), state.get("previous")), ("chained", "/opt/employer-hooks"))
        self.assertEqual(self.run_cmd("remove"), 0)
        got = subprocess.run(["git", "-C", path, "config", "core.hooksPath"], stdout=subprocess.PIPE, text=True).stdout.strip()
        self.assertEqual(got, "/opt/employer-hooks")


if __name__ == "__main__":
    unittest.main()
