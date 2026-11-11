import os
import subprocess
import sys
from pathlib import Path

import pytest

from lantern import cli, progress


def test_main_interrupt_stops_progress_and_exits_130(monkeypatch, capsys):
    def operation():
        with progress.Busy("Running", lambda text: None, interval=0.01) as task:
            tasks.append(task)
            raise KeyboardInterrupt
    tasks = []
    monkeypatch.setattr(cli, "_main", operation)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 130
    assert not tasks[0].thread.is_alive() and not progress.active()
    assert capsys.readouterr().err == "\nCancelled.\n"


def test_subprocess_interrupt_has_no_python_traceback():
    root = Path(__file__).resolve().parents[1]
    script = "from lantern import cli; cli._main = lambda: (_ for _ in ()).throw(KeyboardInterrupt()); cli.main()"
    result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(root / "src")))
    assert result.returncode == 130
    assert result.stderr == "\nCancelled.\n"
