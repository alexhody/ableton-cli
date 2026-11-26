from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

import remote_script.AbletonCliRemote.control_surface as control_surface_module
from remote_script.AbletonCliRemote.remote_config import RemoteConfig
from remote_script.AbletonCliRemote.server import CommandExecutionError


class _CommandServerStub:
    def __init__(
        self,
        host: str,
        port: int,
        command_executor: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
        event_subscriber: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
        event_unsubscriber: Callable[[Any], None] | None = None,
        *,
        max_pending_requests: int,
    ) -> None:
        self.host = host
        self.port = port
        self.command_executor = command_executor
        self.event_subscriber = event_subscriber
        self.event_unsubscriber = event_unsubscriber
        self.max_pending_requests = max_pending_requests
        self.polls: list[float | None] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def poll(self, budget_s: float | None = None) -> None:
        self.polls.append(budget_s)


def _make_surface(
    monkeypatch: pytest.MonkeyPatch,
    *,
    remote_config: RemoteConfig | None = None,
) -> control_surface_module.AbletonCliRemoteSurface:
    monkeypatch.setattr(control_surface_module, "AbletonCommandServer", _CommandServerStub)
    monkeypatch.setattr(
        control_surface_module,
        "load_remote_config",
        lambda: remote_config or RemoteConfig(),
    )
    return control_surface_module.AbletonCliRemoteSurface(object())


def test_surface_starts_command_server_with_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _make_surface(
        monkeypatch,
        remote_config=RemoteConfig(host="0.0.0.0", port=9999, auth_token=None),
    )

    try:
        assert surface._command_server.host == "0.0.0.0"
        assert surface._command_server.port == 9999
    finally:
        surface.disconnect()


def test_surface_rejects_command_with_missing_or_wrong_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        control_surface_module,
        "dispatch_command",
        lambda _backend, name, args: {"handled": name, "args": args},
    )
    surface = _make_surface(
        monkeypatch,
        remote_config=RemoteConfig(host="127.0.0.1", port=8765, auth_token="expected-token"),
    )

    try:
        with pytest.raises(CommandExecutionError) as missing_token:
            surface._execute_command(
                "song_info",
                {},
                {"request_timeout_ms": 100},
            )
        assert missing_token.value.code == "UNAUTHORIZED"

        with pytest.raises(CommandExecutionError) as wrong_token:
            surface._execute_command(
                "song_info",
                {},
                {"request_timeout_ms": 100, "auth_token": "wrong-token"},
            )
        assert wrong_token.value.code == "UNAUTHORIZED"
    finally:
        surface.disconnect()


def test_surface_rejects_non_string_auth_token_without_raising_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        control_surface_module,
        "dispatch_command",
        lambda _backend, name, args: {"handled": name, "args": args},
    )
    surface = _make_surface(
        monkeypatch,
        remote_config=RemoteConfig(host="127.0.0.1", port=8765, auth_token="expected-token"),
    )

    try:
        with pytest.raises(CommandExecutionError) as non_string_token:
            surface._execute_command(
                "song_info",
                {},
                {"request_timeout_ms": 100, "auth_token": 123},
            )
        assert non_string_token.value.code == "UNAUTHORIZED"
    finally:
        surface.disconnect()


def test_surface_allows_command_with_matching_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        control_surface_module,
        "dispatch_command",
        lambda _backend, name, args: {"handled": name, "args": args},
    )
    surface = _make_surface(
        monkeypatch,
        remote_config=RemoteConfig(host="127.0.0.1", port=8765, auth_token="expected-token"),
    )

    try:
        result = surface._execute_command(
            "song_info",
            {},
            {"request_timeout_ms": 100, "auth_token": "expected-token"},
        )
    finally:
        surface.disconnect()

    assert result == {"handled": "song_info", "args": {}}


def test_surface_wires_its_executor_and_queue_bound_into_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _make_surface(monkeypatch)

    try:
        server = surface._command_server
        assert server.started is True
        assert server.command_executor == surface._execute_command
        assert server.max_pending_requests == surface.MAX_PENDING_COMMANDS
    finally:
        surface.disconnect()

    assert server.stopped is True


def test_update_display_polls_the_server_inside_the_component_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _make_surface(monkeypatch)
    guard_depth: list[int] = []
    depth = 0

    @contextmanager
    def _guard() -> Iterator[None]:
        nonlocal depth
        depth += 1
        try:
            yield
        finally:
            depth -= 1

    surface.component_guard = _guard  # type: ignore[method-assign]
    server = surface._command_server
    original_poll = server.poll

    def _poll(budget_s: float | None = None) -> None:
        guard_depth.append(depth)
        original_poll(budget_s)

    server.poll = _poll

    try:
        surface.update_display()
        surface.update_display()
    finally:
        surface.disconnect()

    assert server.polls == [control_surface_module.DRAIN_BUDGET_S] * 2
    assert guard_depth == [1, 1]


def test_surface_dispatches_on_the_calling_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[str] = []

    def _dispatch(_backend: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        threads.append(threading.current_thread().name)
        return {"handled": name, "args": args}

    monkeypatch.setattr(control_surface_module, "dispatch_command", _dispatch)
    surface = _make_surface(monkeypatch)

    try:
        result = surface._execute_command(
            "song_info", {"include_devices": True}, {"request_timeout_ms": 100}
        )
    finally:
        surface.disconnect()

    assert result == {"handled": "song_info", "args": {"include_devices": True}}
    assert threads == [threading.current_thread().name]


def test_a_failing_command_surfaces_as_an_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _dispatch(_backend: Any, name: str, _args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("boom")

    monkeypatch.setattr(control_surface_module, "dispatch_command", _dispatch)
    surface = _make_surface(monkeypatch)

    try:
        with pytest.raises(CommandExecutionError) as exc_info:
            surface._execute_command("song_info", {}, {})
    finally:
        surface.disconnect()

    assert exc_info.value.code == "INTERNAL_ERROR"
    assert exc_info.value.message == "boom"


def _run_keyed(
    surface: control_surface_module.AbletonCliRemoteSurface,
    name: str,
    key: str | None,
) -> dict[str, Any] | CommandExecutionError:
    meta = {} if key is None else {"idempotency_key": key}
    try:
        return surface._execute_command(name, {}, meta)
    except CommandExecutionError as exc:
        return exc


def _count_dispatches(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[str]:
    dispatched: list[str] = []

    def _dispatch(_backend: Any, name: str, _args: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(name)
        return dict(result)

    monkeypatch.setattr(control_surface_module, "dispatch_command", _dispatch)
    return dispatched


def test_repeating_an_idempotency_key_replays_instead_of_dispatching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _make_surface(monkeypatch)
    dispatched = _count_dispatches(monkeypatch, {"tempo": 120.0})

    first = _run_keyed(surface, "song_info", "key-1")
    second = _run_keyed(surface, "song_info", "key-1")

    assert dispatched == ["song_info"]
    assert first == {"tempo": 120.0}
    assert second == {"tempo": 120.0, "idempotent_replay": True}


def test_requests_without_a_key_are_always_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _make_surface(monkeypatch)
    dispatched = _count_dispatches(monkeypatch, {"tempo": 120.0})

    _run_keyed(surface, "song_info", None)
    _run_keyed(surface, "song_info", None)

    assert dispatched == ["song_info", "song_info"]


def test_a_failed_command_is_replayed_as_the_same_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remote_script.AbletonCliRemote.command_backend import CommandError

    surface = _make_surface(monkeypatch)
    dispatched: list[str] = []

    def _dispatch(_backend: Any, name: str, _args: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(name)
        raise CommandError(code="INVALID_ARGUMENT", message="bad track", hint="fix it")

    monkeypatch.setattr(control_surface_module, "dispatch_command", _dispatch)

    first = _run_keyed(surface, "track_volume_set", "key-1")
    second = _run_keyed(surface, "track_volume_set", "key-1")

    assert dispatched == ["track_volume_set"]
    assert isinstance(first, CommandExecutionError)
    assert first.details is None
    assert isinstance(second, CommandExecutionError)
    assert second.code == "INVALID_ARGUMENT"
    assert second.message == "bad track"
    assert second.hint == "fix it"
    assert second.details == {"idempotent_replay": True}


def test_response_cache_evicts_the_oldest_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = _make_surface(monkeypatch)
    monkeypatch.setattr(control_surface_module, "IDEMPOTENCY_CACHE_SIZE", 3)
    dispatched = _count_dispatches(monkeypatch, {"tempo": 120.0})

    for index in range(4):
        _run_keyed(surface, "song_info", f"key-{index}")
    assert len(dispatched) == 4
    assert list(surface._response_cache) == ["key-1", "key-2", "key-3"]

    # key-0 fell out of the cache, so its retry has to run again.
    evicted_retry = _run_keyed(surface, "song_info", "key-0")
    # key-3 is still cached, so its retry is replayed.
    cached_retry = _run_keyed(surface, "song_info", "key-3")

    assert len(dispatched) == 5
    assert evicted_retry == {"tempo": 120.0}
    assert cached_retry == {"tempo": 120.0, "idempotent_replay": True}


def test_a_pending_result_is_cached_for_replay_once_it_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remote_script.AbletonCliRemote.pending import PendingResult

    surface = _make_surface(monkeypatch)
    playing = {"value": False}
    pending = PendingResult(
        ready=lambda: playing["value"],
        result=lambda: {"is_playing": playing["value"]},
        timeout_s=1.0,
    )
    monkeypatch.setattr(control_surface_module, "dispatch_command", lambda *_args: pending)

    first = _run_keyed(surface, "transport_play", "key-1")
    assert first is pending
    playing["value"] = True
    pending.resolve()  # what the server does on a later tick

    replay = _run_keyed(surface, "transport_play", "key-1")
    assert replay == {"is_playing": True, "idempotent_replay": True}
