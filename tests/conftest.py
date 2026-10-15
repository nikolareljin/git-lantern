"""Test-wide setup.

git exports GIT_DIR, GIT_INDEX_FILE and friends to hooks. When the pre-commit
hook runs this suite, a test's `git init` / `git commit` in its temp directory
would otherwise act on the repository being committed to (it did: core.bare
was set and the branch moved to a test commit). Clear them before any test runs.
"""
import os

GIT_REPO_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_QUARANTINE_PATH",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)

for _name in GIT_REPO_VARS:
    os.environ.pop(_name, None)
