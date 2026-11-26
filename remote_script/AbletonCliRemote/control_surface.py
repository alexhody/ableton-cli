from __future__ import annotations

import hmac
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from .command_backend import CommandError, RemoteErrorCode, dispatch_command
from .events import EVENT_NAMES, EventBroker, Subscription, UnknownEventError
from .live_backend import LiveBackend
from .live_events import LiveEventSource
from .remote_config import load_remote_config
from .server import AbletonCommandServer, CommandExecutionError

try:
    from _Framework.ControlSurface import ControlSurface as _ControlSurface  # type: ignore
except Exception:  # pragma: no cover - only used outside Ableton for local checks

    class _ControlSurface:  # type: ignore[too-many-ancestors]
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def update_display(self) -> None:
            pass

        def component_guard(self):  # noqa: ANN201
            return nullcontext()


#: Time one display tick may spend running commands on Live's main thread.
#:
#: Commands run from ``update_display``, on the same thread that renders Live's
#: UI and services audio, and up to ``MAX_PENDING_COMMANDS`` requests can be
#: waiting — running them all inside one tick would freeze the UI or drop
#: audio. 5 ms is about a third of a 60 Hz frame: long enough that an ordinary
#: burst clears in one or two ticks, short enough to stay imperceptible.
#: Requests past the budget are not dropped; they run on the next tick.
DRAIN_BUDGET_S = 0.005


#: How many completed responses are kept for idempotency-key replay.
#: A retry follows within one request timeout, so only recent keys can ever be
#: hit; 256 covers a deep `batch stream` burst while keeping the cache small
#: enough to be irrelevant to Live's memory. Eviction is FIFO by insertion.
IDEMPOTENCY_CACHE_SIZE = 256

#: Reserved result/details field marking a response that was replayed from the
#: idempotency cache instead of being executed again. No command handler may
#: return a result key by this name.
IDEMPOTENT_REPLAY_FIELD = "idempotent_replay"


@dataclass(slots=True)
class _CachedResponse:
    """A completed dispatch, kept so a retry can replay it instead of rerunning."""

    result: dict[str, Any] | None = None
    error: CommandError | None = None


def _as_command_error(error: Exception) -> CommandError:
    """Normalize a dispatch failure for caching and for the protocol boundary."""
    if isinstance(error, CommandError):
        return error
    return CommandError(
        code=RemoteErrorCode.INTERNAL_ERROR.value,
        message=str(error),
        hint="Check Ableton Log.txt for details.",
    )


def _execution_error(error: CommandError) -> CommandExecutionError:
    return CommandExecutionError(
        code=error.code,
        message=error.message,
        hint=error.hint,
        details=error.details,
    )


class AbletonCliRemoteSurface(_ControlSurface):
    """Ableton Control Surface that exposes a local command server.

    All socket work and every command run on Live's main thread, from
    ``update_display`` (see ``AbletonCommandServer``).
    """

    MAX_PENDING_COMMANDS = 512

    def __init__(self, c_instance):  # noqa: ANN001
        super().__init__(c_instance)
        self._backend = LiveBackend(self)
        # Used only from `_execute_command`, i.e. only on Live's main thread,
        # which serializes dispatch — so no lock is needed and no request can
        # observe another one mid-flight under the same key.
        self._response_cache: OrderedDict[str, _CachedResponse] = OrderedDict()
        remote_config = load_remote_config()
        self._auth_token = remote_config.auth_token
        self._event_broker = EventBroker()
        # Listeners are registered here, on Live's main thread, once.
        self._event_source = LiveEventSource(lambda: self.song(), self._event_broker)
        self._available_events = self._event_source.attach()
        self._command_server = AbletonCommandServer(
            host=remote_config.host,
            port=remote_config.port,
            command_executor=self._execute_command,
            event_subscriber=self._subscribe,
            event_unsubscriber=self._event_broker.unsubscribe,
            max_pending_requests=self.MAX_PENDING_COMMANDS,
        )
        self._command_server.start()

    @property
    def available_events(self) -> tuple[str, ...]:
        return self._available_events

    def _require_auth(self, meta: dict[str, Any]) -> None:
        provided_auth_token = meta.get("auth_token")
        if self._auth_token is not None and (
            not isinstance(provided_auth_token, str)
            or not hmac.compare_digest(provided_auth_token, self._auth_token)
        ):
            raise CommandExecutionError(
                code=RemoteErrorCode.UNAUTHORIZED.value,
                message="Missing or invalid auth token",
                hint=(
                    "Set the same auth_token in the CLI config and in "
                    "AbletonCliRemote/remote_config.json."
                ),
            )

    def _subscribe(self, args: dict[str, Any], meta: dict[str, Any]) -> Subscription:
        self._require_auth(meta)
        requested = args.get("events", list(self._available_events))
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            raise CommandExecutionError(
                code=RemoteErrorCode.INVALID_ARGUMENT.value,
                message="events must be a list of event names",
                hint=f"Use any of: {', '.join(self._available_events) or '(none available)'}.",
            )
        names = requested or list(self._available_events)
        unavailable = [name for name in names if name not in self._available_events]
        if unavailable:
            raise CommandExecutionError(
                code=RemoteErrorCode.INVALID_ARGUMENT.value,
                message=f"events not available in this Live version: {sorted(unavailable)}",
                hint=f"Use any of: {', '.join(self._available_events) or '(none available)'}.",
                details={
                    "available_events": list(self._available_events),
                    "known_events": list(EVENT_NAMES),
                },
            )
        try:
            return self._event_broker.subscribe(names)
        except UnknownEventError as exc:
            raise CommandExecutionError(
                code=RemoteErrorCode.INVALID_ARGUMENT.value,
                message=str(exc),
                hint=f"Use any of: {', '.join(EVENT_NAMES)}.",
            ) from exc

    def _execute_command(
        self, name: str, args: dict[str, Any], meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one command on Live's main thread, or replay its stored outcome."""
        self._require_auth(meta)
        key = meta.get("idempotency_key")
        cached = self._replay_cached_response(key)
        if cached is not None:
            return cached
        try:
            result = dispatch_command(self._backend, name, args)
        except Exception as exc:  # noqa: BLE001
            error = _as_command_error(exc)
            # Recorded even when the client has already given up waiting — that
            # abandoned response is exactly what a retry needs to replay
            # instead of applying the command a second time.
            self._remember_response(key, _CachedResponse(error=error))
            raise _execution_error(error) from exc
        self._remember_response(key, _CachedResponse(result=result))
        return result

    def _remember_response(self, key: str | None, entry: _CachedResponse) -> None:
        if key is None:
            return
        self._response_cache.pop(key, None)
        self._response_cache[key] = entry
        while len(self._response_cache) > IDEMPOTENCY_CACHE_SIZE:
            self._response_cache.popitem(last=False)

    def _replay_cached_response(self, key: str | None) -> dict[str, Any] | None:
        """The stored result when this key already ran; raises a stored failure."""
        if key is None:
            return None
        entry = self._response_cache.get(key)
        if entry is None:
            return None
        if entry.error is not None:
            raise CommandExecutionError(
                code=entry.error.code,
                message=entry.error.message,
                hint=entry.error.hint,
                details={**(entry.error.details or {}), IDEMPOTENT_REPLAY_FIELD: True},
            )
        return {**(entry.result or {}), IDEMPOTENT_REPLAY_FIELD: True}

    def update_display(self) -> None:
        """Live's ~100 ms tick: serve the sockets here, on the main thread."""
        super().update_display()
        with self.component_guard():
            self._command_server.poll(budget_s=DRAIN_BUDGET_S)

    def disconnect(self) -> None:
        self._command_server.stop()
        self._event_source.detach()
        self._event_broker.close()
        super().disconnect()
