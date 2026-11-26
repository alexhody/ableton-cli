"""Timeouts end to end: the real server's deadline checks plus the surface's replay cache."""

from __future__ import annotations

import functools
import json
from typing import Any

import pytest

import remote_script.AbletonCliRemote.control_surface as control_surface_module
from remote_script.AbletonCliRemote.command_backend import PROTOCOL_VERSION
from remote_script.AbletonCliRemote.remote_config import RemoteConfig
from remote_script.AbletonCliRemote.server import AbletonCommandServer


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _make_surface(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> control_surface_module.AbletonCliRemoteSurface:
    monkeypatch.setattr(
        control_surface_module,
        "AbletonCommandServer",
        functools.partial(AbletonCommandServer, clock=clock),
    )
    monkeypatch.setattr(
        control_surface_module,
        "load_remote_config",
        lambda: RemoteConfig(host="127.0.0.1", port=0),
    )
    return control_surface_module.AbletonCliRemoteSurface(object())


def _line(meta: dict[str, Any]) -> bytes:
    payload = {
        "type": "command",
        "name": "add_notes_to_clip",
        "args": {"notes": []},
        "meta": {"request_timeout_ms": 1000, **meta},
        "request_id": "request-1",
        "protocol_version": PROTOCOL_VERSION,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def _send(
    surface: control_surface_module.AbletonCliRemoteSurface,
    meta: dict[str, Any],
    *,
    received_at: float,
) -> dict[str, Any]:
    _keep_open, response, _subscription = surface._command_server._handle_line(  # noqa: SLF001
        _line(meta), received_at
    )
    return response


def _slow_dispatch(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, calls: list[str], *, seconds: float
) -> None:
    def _dispatch(_backend: Any, name: str, _args: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        clock.now += seconds
        return {"note_count": 1}

    monkeypatch.setattr(control_surface_module, "dispatch_command", _dispatch)


def test_retry_after_an_overrun_with_the_same_key_does_not_apply_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    calls: list[str] = []
    _slow_dispatch(monkeypatch, clock, calls, seconds=2.0)
    surface = _make_surface(monkeypatch, clock)

    try:
        first = _send(surface, {"idempotency_key": "step-key"}, received_at=clock.now)
        retry = _send(surface, {"idempotency_key": "step-key"}, received_at=clock.now)
    finally:
        surface.disconnect()

    assert first["error"]["code"] == "TIMEOUT"
    assert first["error"]["details"]["may_have_executed"] is True
    assert calls == ["add_notes_to_clip"]
    assert retry["result"] == {"note_count": 1, "idempotent_replay": True}


def test_retry_after_an_overrun_without_a_key_still_applies_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    calls: list[str] = []
    _slow_dispatch(monkeypatch, clock, calls, seconds=2.0)
    surface = _make_surface(monkeypatch, clock)

    try:
        _send(surface, {}, received_at=clock.now)
        _send(surface, {}, received_at=clock.now)
    finally:
        surface.disconnect()

    # Without a key the Remote Script cannot recognise the resend. This is
    # why `_execute_step` always carries one across a retry.
    assert calls == ["add_notes_to_clip", "add_notes_to_clip"]


def test_request_that_expired_while_waiting_leaves_nothing_to_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    calls: list[str] = []
    _slow_dispatch(monkeypatch, clock, calls, seconds=0.0)
    surface = _make_surface(monkeypatch, clock)

    try:
        expired = _send(surface, {"idempotency_key": "step-key"}, received_at=clock.now - 5.0)
        retry = _send(surface, {"idempotency_key": "step-key"}, received_at=clock.now)
    finally:
        surface.disconnect()

    assert expired["error"]["code"] == "TIMEOUT"
    assert expired["error"]["details"] == {"request_timeout_ms": 1000, "may_have_executed": False}
    # Never dispatched, so the retry actually runs instead of replaying.
    assert calls == ["add_notes_to_clip"]
    assert retry["result"] == {"note_count": 1}
