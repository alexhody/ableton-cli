"""Drive an AbletonCommandServer the way Live's display tick does, for socket tests."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from remote_script.AbletonCliRemote.server import AbletonCommandServer


@contextmanager
def serving(server: AbletonCommandServer, *, interval_s: float = 0.001) -> Iterator[int]:
    """Start the server, poll it from a background thread, yield its port."""
    server.start()
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            server.poll()
            stop.wait(interval_s)

    thread = threading.Thread(target=_loop, name="remote-server-poll", daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        stop.set()
        thread.join(timeout=2.0)
        server.stop()
