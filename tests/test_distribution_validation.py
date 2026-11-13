import importlib.util
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "distribution_check", Path(__file__).resolve().parents[1] / "scripts/check_package.py",
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


@pytest.mark.parametrize("output,is_warning", [
    ("Building /tmp/WARNING:example", False),
    ("Building DeprecationWarning fixtures", False),
    ("WARNING: package does not provide the extra", True),
    ("module.py:12: DeprecationWarning: obsolete metadata", True),
    ("DeprecationWarning: obsolete metadata", True),
    ("\x1b[33mWARNING: package does not provide the extra\x1b[0m", True),
    ("module.py:12: FutureWarning: obsolete metadata", True),
    ("\x1b[32mBuilding /tmp/WARNING:example\x1b[0m", False),
])
def test_legitimate_paths_and_actual_warnings_are_distinct(output, is_warning):
    command = [sys.executable, "-c", "print({!r})".format(output)]
    if is_warning:
        with pytest.raises(RuntimeError, match="Warning|WARNING"):
            CHECK.run(command)
    else:
        assert CHECK.run(command) == output


def test_failed_command_keeps_its_exit_code():
    with pytest.raises(RuntimeError, match="exit 7"):
        CHECK.run([sys.executable, "-c", "raise SystemExit(7)"])
