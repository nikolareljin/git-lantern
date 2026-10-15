"""Test-wide setup.

git exports GIT_DIR, GIT_INDEX_FILE and friends to hooks (and GIT_CONFIG_PARAMETERS
for `git -c ... commit`). When the pre-commit hook runs this suite, a test's
`git init` / `git commit` in its temp directory would otherwise act on the
repository being committed to (it did: core.bare was set and the branch moved
to a test commit). Clear them before any test runs: the names
`git rev-parse --local-env-vars` prints, plus the six a hook always gets.
"""
import os
import subprocess

ALWAYS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


def git_repo_vars():
    try:
        out = subprocess.run(["git", "rev-parse", "--local-env-vars"], check=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout
    except OSError:
        out = ""
    return set(out.split()) | set(ALWAYS)


for _name in git_repo_vars():
    os.environ.pop(_name, None)
