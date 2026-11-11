"""Local maintenance drain. The existing queue and its grants stay authoritative."""
from pathlib import Path
import json
import os
import time


def drain_requested(directory):
    return (Path(directory) / 'drain.request').exists()


def wait_before_start(directory):
    if drain_requested(directory):
        print(json.dumps({'lifecycle': {'state': 'startup_held', 'pid': os.getpid()}}), flush=True)
    while drain_requested(directory):
        time.sleep(1)


def finish_threads(threads, directory, hard_stop, *, grace_seconds=20):
    """Finish admitted work before an operator requests the platform restart.

    A local drain has no timeout: the caller must see drained before deploying.
    SIGTERM has a bounded grace period and reports unfinished thread names.
    """
    deadline = None
    while True:
        alive = [thread for thread in threads if thread.is_alive()]
        if not alive:
            return []
        if hard_stop() and deadline is None:
            deadline = time.monotonic() + grace_seconds
            for thread in alive:
                if isinstance(thread, ChildProcess):
                    thread.request_stop()
        if deadline is not None and time.monotonic() >= deadline:
            return [thread.name for thread in alive]
        for thread in alive:
            thread.join(timeout=0.1)


class ChildProcess:
    def __init__(self, process):
        self.process = process
        self.name = 'msw-combo-factory'

    def is_alive(self):
        return self.process.poll() is None

    def request_stop(self):
        if self.is_alive():
            self.process.terminate()

    def join(self, timeout):
        import subprocess
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def write_status(directory, state, **details):
    data = {'state': state, 'pid': os.getpid(), 'at': int(time.time()), **details}
    target = Path(directory) / 'drain-status.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.replace(target)
    print(json.dumps({'lifecycle': data}), flush=True)
