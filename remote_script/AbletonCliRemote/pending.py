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

DoneCallback = Callable[[dict[str, Any] | None, Exception | None], None]


class PendingResult:
    """A result read on a later tick. It resolves once; later reads repeat that outcome."""

    __slots__ = ("_ready", "_result", "timeout_s", "_callbacks", "_outcome")

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
        self._callbacks: list[DoneCallback] = []
        self._outcome: tuple[dict[str, Any] | None, Exception | None] | None = None

    @property
    def done(self) -> bool:
        return self._outcome is not None

    def ready(self) -> bool:
        return self.done or bool(self._ready())

    def add_done_callback(self, callback: DoneCallback) -> None:
        """Called once with ``(result, None)`` or ``(None, error)`` when this resolves."""
        self._callbacks.append(callback)

    def resolve(self) -> dict[str, Any]:
        """Read the result now, whether or not it is ready, and keep that outcome."""
        if self._outcome is None:
            try:
                self._outcome = (self._result(), None)
            except Exception as exc:  # noqa: BLE001 - kept and re-raised below
                self._outcome = (None, exc)
            for callback in self._callbacks:
                callback(*self._outcome)
        result, error = self._outcome
        if error is not None:
            raise error
        return result  # type: ignore[return-value]


def resolve_now(value: Any) -> Any:
    """Resolve a pending result at once, for callers that cannot wait a tick (batch steps)."""
    return value.resolve() if isinstance(value, PendingResult) else value
