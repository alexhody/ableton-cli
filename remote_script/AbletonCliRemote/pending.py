"""Results that Live can only produce after the current tick.

Commands run on Live's main thread, inside ``update_display``. Some changes,
like starting or stopping the transport, take effect only once control returns
to Live, so a handler that waits for them in a loop blocks Live and never sees
them. Such a handler returns a :class:`PendingResult` instead: the server
answers once ``ready()`` holds, checked on each later tick, or when
``timeout_s`` has passed, with whatever ``result()`` then reports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PendingResult:
    __slots__ = ("_ready", "_result", "timeout_s", "_callbacks")

    def __init__(
        self,
        *,
        ready: Callable[[], bool],
        result: Callable[[], dict[str, Any]],
        timeout_s: float,
    ) -> None:
        self._ready = ready
        self._result = result
        self.timeout_s = timeout_s
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []

    def ready(self) -> bool:
        return bool(self._ready())

    def add_done_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Called with the result when the pending result resolves."""
        self._callbacks.append(callback)

    def resolve(self) -> dict[str, Any]:
        """Read the result now, whether or not it is ready."""
        result = self._result()
        for callback in self._callbacks:
            callback(result)
        return result


def resolve_now(value: Any) -> Any:
    """Resolve a pending result at once, for callers that cannot wait a tick (batch steps)."""
    return value.resolve() if isinstance(value, PendingResult) else value
