"""Scoped progress feedback for synchronous background work."""

import contextvars
import signal
import threading
import time


_current = contextvars.ContextVar("lantern_progress", default=None)


class TaskCancelled(BaseException):
    """Stop one task without treating cancellation as an operation failure."""


class TaskCancellation:
    """Deliver a task stop to the main thread, including during blocking I/O."""

    def __enter__(self):
        self.previous = signal.signal(signal.SIGUSR1, self.handle)
        return self

    def handle(self, signum, frame):
        raise TaskCancelled

    def request(self):
        if threading.current_thread() is threading.main_thread():
            raise TaskCancelled
        signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)

    def __exit__(self, exc_type, exc, tb):
        signal.signal(signal.SIGUSR1, self.previous)


def active():
    return _current.get() is not None


def report(message):
    task = _current.get()
    if task is None:
        return False
    with task.lock:
        task.message = message
        task.detail = ""
    return True


def detail(message):
    task = _current.get()
    if task is not None:
        with task.lock:
            task.detail = message


class Busy:
    """Refresh a status display while work runs; stop before the next prompt."""

    def __init__(self, heading, render=None, interval=0.5):
        self.heading = heading
        self.render = render
        self.interval = interval
        self.message = ""
        self.detail = ""
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.frame = 0

    def paint(self):
        with self.lock:
            text = "\n\n".join(part for part in (self.heading, self.message, self.detail) if part)
        elapsed = int(time.monotonic() - self.started)
        text += "\n\nWorking [{}]  Elapsed: {}s".format("|/-\\"[self.frame % 4], elapsed)
        self.render(text)
        self.frame += 1

    def heartbeat(self):
        while not self.stop.wait(self.interval):
            try:
                self.paint()
            except OSError:
                return

    def __enter__(self):
        self.parent = _current.get()
        if self.parent is not None:
            report(self.heading)
            return self.parent
        self.started = time.monotonic()
        if self.render is not None:
            self.paint()
        self.token = _current.set(self)
        if self.render is not None:
            self.thread = threading.Thread(target=self.heartbeat, daemon=True)
            try:
                self.thread.start()
            except BaseException:
                self.stop.set()
                _current.reset(self.token)
                raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.parent is not None:
            return
        try:
            self.stop.set()
            if self.thread is not None:
                self.thread.join()
        finally:
            _current.reset(self.token)
