"""The suite must not touch the repository a git hook runs it for.

git runs pre-commit with GIT_DIR and GIT_INDEX_FILE pointing at the repository
being committed to. This runs a test that makes its own commits under that
environment, aimed at a decoy repository, and checks the decoy is untouched.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INNER = "tests/test_hooks_cmd.py::OwnershipAndApply::test_apply_is_idempotent_and_adds_agents_once"


def _git(*args):
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, text=True,
                          env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")}).stdout.strip()


class HookEnvironment(unittest.TestCase):
    def test_a_hook_environment_does_not_reach_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            decoy = os.path.join(tmp, "decoy")
            _git("init", "-q", decoy)
            _git("-C", decoy, "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "-q", "--allow-empty", "-m", "decoy")
            before = (_git("-C", decoy, "rev-parse", "HEAD"), open(os.path.join(decoy, ".git", "config")).read())
            env = dict(os.environ, GIT_DIR=os.path.join(decoy, ".git"),
                       GIT_INDEX_FILE=os.path.join(decoy, ".git", "index"),
                       PYTHONPATH=os.path.join(os.path.dirname(HERE), "src"))
            run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", INNER],
                                 cwd=os.path.dirname(HERE), env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)
            after = (_git("-C", decoy, "rev-parse", "HEAD"), open(os.path.join(decoy, ".git", "config")).read())
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
