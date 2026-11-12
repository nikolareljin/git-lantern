import os
import select
import shutil
import signal
import subprocess
import sys
import time
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


@pytest.mark.skipif(os.name != "posix" or not shutil.which("dialog"), reason="requires a POSIX terminal and dialog")
@pytest.mark.parametrize("key,expected,delay", [(b"\x1b", 0, 0), (b"\r", 0, 0), (b"\x03", 130, 0), (b"\r", 0, 2)])
def test_real_terminal_task_stop_and_application_interrupt(key, expected, delay):
    import pty

    root = Path(__file__).resolve().parents[1]
    script = '''import threading
from lantern import cli, progress
def operation():
    print("OPERATION_STARTED", flush=True)
    threading.Event().wait(30)
def run():
    try:
        cli._dialog_work("Cancellation test", "Waiting for stop", operation)
    except progress.TaskCancelled:
        assert not progress.active()
        print("APPLICATION_ALIVE", flush=True)
cli._main = run
cli.main()
'''
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(root)
        os.execve(sys.executable, [sys.executable, "-c", script],
                  dict(os.environ, TERM="xterm", PYTHONPATH=str(root / "src")))
    output = b""
    deadline = time.monotonic() + 15
    sent = False
    control_at = None
    status = None
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    output += os.read(fd, 65536)
                except OSError:
                    pass
            if control_at is None and b"OPERATION_STARTED" in output and b"Ctrl+C:" in output:
                control_at = time.monotonic()
            if not sent and control_at is not None and time.monotonic() - control_at >= delay:
                os.write(fd, key)
                sent = True
            ended, status_value = os.waitpid(pid, os.WNOHANG)
            if ended:
                status = status_value
                break
        if status is None:
            ended, status_value = os.waitpid(pid, os.WNOHANG)
            if ended:
                status = status_value
        assert sent and status is not None, output[-4096:]
        assert os.waitstatus_to_exitcode(status) == expected, output[-4096:]
        assert b"Traceback" not in output
        assert (b"APPLICATION_ALIVE" in output) is (expected == 0)
    finally:
        os.close(fd)
        if status is None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
