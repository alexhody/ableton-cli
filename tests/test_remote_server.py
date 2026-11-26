from __future__ import annotations

import json
import select
import socket
import time
from collections.abc import Callable
from typing import Any

import pytest

from remote_script.AbletonCliRemote.command_backend import PROTOCOL_VERSION
from remote_script.AbletonCliRemote.pending import PendingResult
from remote_script.AbletonCliRemote.server import (
    AbletonCommandServer,
    CommandExecutionError,
    _parse_command_request,
)
from tests.remote_server_loop import serving


def test_parse_command_request_accepts_strict_protocol_shape() -> None:
    request = _parse_command_request(
        {
            "type": "command",
            "name": "song_info",
            "args": {},
            "meta": {"request_timeout_ms": 15000},
            "request_id": "request-1",
            "protocol_version": 3,
        }
    )

    assert request == ("request-1", "command", "song_info", {}, {"request_timeout_ms": 15000})


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {
            "type": "command",
            "name": "song_info",
            "args": {},
            "meta": {},
            "request_id": "request-1",
            "protocol_version": 3,
            "extra": "not allowed",
        },
        {
            "type": "command",
            "name": "",
            "args": {},
            "meta": {},
            "request_id": "request-1",
            "protocol_version": 3,
        },
        {
            "type": "command",
            "name": "song_info",
            "args": [],
            "meta": {},
            "request_id": "request-1",
            "protocol_version": 3,
        },
        {
            "type": "command",
            "name": "song_info",
            "args": {},
            "meta": {},
            "request_id": "request-1",
            "protocol_version": True,
        },
    ],
)
def test_parse_command_request_rejects_invalid_protocol_shape(payload: object) -> None:
    with pytest.raises(CommandExecutionError) as exc_info:
        _parse_command_request(payload)

    assert exc_info.value.code == "INVALID_ARGUMENT"


def _server(
    executor: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
    **kwargs: Any,
) -> AbletonCommandServer:
    return AbletonCommandServer(host="127.0.0.1", port=0, command_executor=executor, **kwargs)


def test_handle_returns_invalid_argument_for_malformed_json() -> None:
    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("command_executor must not run for malformed JSON")

    with serving(_server(_executor)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(b"not json\n")
            with sock.makefile("rb") as file_obj:
                raw = file_obj.readline()

    response = json.loads(raw.decode("utf-8"))

    assert response["ok"] is False
    assert response["error"]["code"] == "INVALID_ARGUMENT"


def _request_line(request_id: str, name: str = "ping", meta: dict[str, Any] | None = None) -> bytes:
    payload = {
        "type": "command",
        "name": name,
        "args": {},
        "meta": meta or {},
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def test_handle_processes_multiple_requests_on_one_connection() -> None:
    calls: list[str] = []

    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        return {"pong": name}

    with serving(_server(_executor)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            with sock.makefile("rwb") as file_obj:
                file_obj.write(_request_line("request-1", "ping"))
                file_obj.flush()
                first = json.loads(file_obj.readline().decode("utf-8"))

                file_obj.write(_request_line("request-2", "ping"))
                file_obj.flush()
                second = json.loads(file_obj.readline().decode("utf-8"))

    assert calls == ["ping", "ping"]
    assert first["ok"] is True
    assert first["request_id"] == "request-1"
    assert second["ok"] is True
    assert second["request_id"] == "request-2"


def test_pipelined_requests_are_answered_in_order() -> None:
    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        return {"name": name}

    with serving(_server(_executor)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(b"".join(_request_line(f"request-{i}") for i in range(5)))
            with sock.makefile("rb") as file_obj:
                ids = [json.loads(file_obj.readline())["request_id"] for _ in range(5)]

    assert ids == [f"request-{i}" for i in range(5)]


def test_request_before_half_close_is_still_answered() -> None:
    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        return {"name": name}

    with serving(_server(_executor)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(_request_line("request-1"))
            sock.shutdown(socket.SHUT_WR)
            with sock.makefile("rb") as file_obj:
                first = json.loads(file_obj.readline())
                trailing = file_obj.readline()

    assert first["request_id"] == "request-1"
    assert trailing == b""


def test_handle_closes_connection_after_malformed_json_line() -> None:
    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("command_executor must not run for malformed JSON")

    with serving(_server(_executor)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            with sock.makefile("rwb") as file_obj:
                file_obj.write(b"not json\n")
                file_obj.flush()
                first = json.loads(file_obj.readline().decode("utf-8"))

                # After the server closes the connection, reading yields a
                # clean EOF on POSIX but may raise ConnectionAbortedError or
                # ConnectionResetError on Windows once the follow-up write
                # has triggered an RST. Both signal the same server behavior.
                try:
                    file_obj.write(_request_line("request-2", "ping"))
                    file_obj.flush()
                    trailing = file_obj.readline()
                except ConnectionError:
                    trailing = b""

    assert first["error"]["code"] == "INVALID_ARGUMENT"
    assert trailing == b""


# -- driven by hand: each poll() stands for one of Live's display ticks ------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _connect_and_send(port: int, *lines: bytes) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    sock.sendall(b"".join(lines))
    return sock


def _settle() -> None:
    """Let loopback data reach the server's receive buffers before the next poll."""
    time.sleep(0.05)


def _read_response(sock: socket.socket) -> dict[str, Any]:
    with sock.makefile("rb") as file_obj:
        return json.loads(file_obj.readline())


def _has_data(sock: socket.socket) -> bool:
    # Loopback delivery is asynchronous on some platforms, so allow a moment.
    # No poll() runs meanwhile, so data here was written by the earlier one.
    readable, _, _ = select.select([sock], [], [], 1.0)
    return bool(readable)


def test_one_poll_accepts_reads_runs_and_answers() -> None:
    calls: list[str] = []
    server = _server(lambda name, args, meta: calls.append(name) or {"name": name})
    server.start()
    try:
        sock = _connect_and_send(server.server_address[1], _request_line("request-1"))
        _settle()

        server.poll()

        # Accepted, read, run and written in a single tick: the answer is
        # already waiting on the client's socket.
        assert calls == ["ping"]
        assert _has_data(sock)
        assert _read_response(sock)["request_id"] == "request-1"
        sock.close()
    finally:
        server.stop()


def test_poll_budget_defers_the_rest_to_the_next_poll() -> None:
    clock = _Clock()
    calls: list[str] = []

    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        clock.now += 0.004
        return {"name": name}

    server = _server(_executor, clock=clock)
    server.start()
    try:
        port = server.server_address[1]
        socks = [_connect_and_send(port, _request_line(f"r{i}", f"c{i}")) for i in range(4)]
        _settle()

        server.poll(budget_s=0.005)
        assert len(calls) == 2  # the budget is checked between commands only

        server.poll(budget_s=0.0)
        assert len(calls) == 3  # an exhausted budget still runs one command

        server.poll(budget_s=None)
        assert sorted(calls) == ["c0", "c1", "c2", "c3"]
        for sock in socks:
            assert _read_response(sock)["ok"] is True
            sock.close()
    finally:
        server.stop()


def test_full_queue_answers_remote_busy_without_running_the_command() -> None:
    calls: list[str] = []
    server = _server(lambda name, args, meta: calls.append(name) or {}, max_pending_requests=1)
    server.start()
    try:
        port = server.server_address[1]
        first = _connect_and_send(port, _request_line("first"))
        second = _connect_and_send(port, _request_line("second"))
        _settle()

        server.poll()

        responses = {r["request_id"]: r for r in (_read_response(first), _read_response(second))}
        first.close()
        second.close()
    finally:
        server.stop()

    assert calls == ["ping"]
    assert responses["first"]["ok"] is True
    busy = responses["second"]["error"]
    assert busy["code"] == "REMOTE_BUSY"
    assert busy["details"] == {"max_pending_commands": 1}


def test_request_whose_deadline_passed_while_waiting_never_runs() -> None:
    clock = _Clock()
    calls: list[str] = []

    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        clock.now += 0.5  # a slow command holds up the one queued behind it
        return {}

    server = _server(_executor, clock=clock)
    server.start()
    try:
        port = server.server_address[1]
        slow = _connect_and_send(port, _request_line("slow", "slow", {"request_timeout_ms": 5000}))
        _settle()
        server.poll()
        assert _read_response(slow)["ok"] is True

        # Queued now; the slow command below runs first and overruns its deadline.
        blocker = _connect_and_send(port, _request_line("blocker", "slow"))
        late = _connect_and_send(port, _request_line("late", "late", {"request_timeout_ms": 100}))
        _settle()
        server.poll()

        timed_out = _read_response(late)["error"]
        assert _read_response(blocker)["ok"] is True
        for sock in (slow, blocker, late):
            sock.close()
    finally:
        server.stop()

    assert calls == ["slow", "slow"]
    assert timed_out["code"] == "TIMEOUT"
    assert timed_out["details"] == {"request_timeout_ms": 100, "may_have_executed": False}


@pytest.mark.parametrize("fails", [False, True])
def test_command_that_overruns_its_deadline_reports_may_have_executed(fails: bool) -> None:
    clock = _Clock()

    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        clock.now += 1.0
        if fails:
            raise CommandExecutionError(code="INVALID_ARGUMENT", message="bad")
        return {}

    server = _server(_executor, clock=clock)
    server.start()
    try:
        sock = _connect_and_send(
            server.server_address[1], _request_line("r", "slow", {"request_timeout_ms": 100})
        )
        _settle()
        server.poll()
        error = _read_response(sock)["error"]
        sock.close()
    finally:
        server.stop()

    assert error["code"] == "TIMEOUT"
    assert error["details"] == {"request_timeout_ms": 100, "may_have_executed": True}


@pytest.mark.parametrize("raw_timeout", ["soon", 0, -5])
def test_invalid_request_timeout_is_rejected(raw_timeout: object) -> None:
    calls: list[str] = []
    server = _server(lambda name, args, meta: calls.append(name) or {})
    server.start()
    try:
        sock = _connect_and_send(
            server.server_address[1], _request_line("r", meta={"request_timeout_ms": raw_timeout})
        )
        _settle()
        server.poll()
        error = _read_response(sock)["error"]
        sock.close()
    finally:
        server.stop()

    assert calls == []
    assert error["code"] == "INVALID_ARGUMENT"


def test_stop_closes_open_connections() -> None:
    server = _server(lambda name, args, meta: {})
    server.start()
    sock = socket.create_connection(server.server_address, timeout=2.0)
    _settle()
    server.poll()

    server.stop()

    try:
        assert sock.recv(1) == b""
    except ConnectionError:
        pass  # Windows reports the closed peer as a reset.
    finally:
        sock.close()


def _command_payload(meta: dict[str, object]) -> dict[str, object]:
    return {
        "type": "command",
        "name": "song_info",
        "args": {},
        "meta": meta,
        "request_id": "request-1",
        "protocol_version": 3,
    }


def test_parse_command_request_accepts_an_idempotency_key() -> None:
    request = _parse_command_request(_command_payload({"idempotency_key": "step-key"}))

    assert request[4] == {"idempotency_key": "step-key"}


@pytest.mark.parametrize("key", ["", 42, None, "x" * 129])
def test_parse_command_request_rejects_an_invalid_idempotency_key(key: object) -> None:
    with pytest.raises(CommandExecutionError) as exc_info:
        _parse_command_request(_command_payload({"idempotency_key": key}))

    assert exc_info.value.code == "INVALID_ARGUMENT"


class _Flag:
    """Live state that a pending result waits for; flipped between polls like a tick."""

    def __init__(self) -> None:
        self.value = False


def _pending_executor(flag: _Flag, calls: list[str], *, timeout_s: float = 1.0):  # noqa: ANN202
    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> Any:
        calls.append(name)
        if name != "transport_play":
            return {"name": name}
        return PendingResult(
            ready=lambda: flag.value,
            result=lambda: {"is_playing": flag.value},
            timeout_s=timeout_s,
        )

    return _executor


def test_pending_result_is_answered_on_the_poll_after_live_applies_it() -> None:
    flag, calls = _Flag(), []
    server = _server(_pending_executor(flag, calls))
    server.start()
    try:
        sock = _connect_and_send(
            server.server_address[1],
            _request_line("play", "transport_play"),
            _request_line("next", "song_info"),
        )
        _settle()

        server.poll()
        # Not answered while Live has not applied it, and the pipelined request waits.
        readable, _, _ = select.select([sock], [], [], 0.1)
        assert not readable
        assert calls == ["transport_play"]

        flag.value = True  # Live applies the change once the tick returns
        server.poll()

        assert _read_response(sock)["result"] == {"is_playing": True}
        server.poll()
        assert calls == ["transport_play", "song_info"]
        sock.close()
    finally:
        server.stop()


def test_pending_result_answers_with_the_current_state_when_its_wait_runs_out() -> None:
    clock, flag, calls = _Clock(), _Flag(), []
    server = _server(_pending_executor(flag, calls, timeout_s=1.0), clock=clock)
    server.start()
    try:
        sock = _connect_and_send(server.server_address[1], _request_line("play", "transport_play"))
        _settle()
        server.poll()
        clock.now += 1.5
        server.poll()

        response = _read_response(sock)
        sock.close()
    finally:
        server.stop()

    assert response["ok"] is True
    assert response["result"] == {"is_playing": False}


def test_pending_result_past_the_request_timeout_reports_may_have_executed() -> None:
    clock, flag, calls = _Clock(), _Flag(), []
    server = _server(_pending_executor(flag, calls, timeout_s=5.0), clock=clock)
    server.start()
    try:
        sock = _connect_and_send(
            server.server_address[1],
            _request_line("play", "transport_play", {"request_timeout_ms": 100}),
        )
        _settle()
        server.poll()
        clock.now += 0.5
        flag.value = True
        server.poll()

        error = _read_response(sock)["error"]
        sock.close()
    finally:
        server.stop()

    assert error["code"] == "TIMEOUT"
    assert error["details"] == {"request_timeout_ms": 100, "may_have_executed": True}


def test_pending_result_that_is_already_ready_is_answered_in_the_same_poll() -> None:
    flag, calls = _Flag(), []
    flag.value = True
    server = _server(_pending_executor(flag, calls))
    server.start()
    try:
        sock = _connect_and_send(server.server_address[1], _request_line("play", "transport_play"))
        _settle()
        server.poll()

        assert _has_data(sock)
        assert _read_response(sock)["result"] == {"is_playing": True}
        sock.close()
    finally:
        server.stop()


def test_pending_result_resolves_once_and_reports_its_outcome_to_callbacks() -> None:
    reads: list[int] = []
    outcomes: list[tuple[Any, Any]] = []
    pending = PendingResult(
        ready=lambda: False,
        result=lambda: reads.append(1) or {"n": len(reads)},
        timeout_s=1.0,
    )
    pending.add_done_callback(lambda result, error: outcomes.append((result, error)))

    assert not pending.done
    assert pending.resolve() == {"n": 1}
    assert pending.resolve() == {"n": 1}
    assert pending.done and pending.ready()
    assert reads == [1]
    assert outcomes == [({"n": 1}, None)]


def test_pending_result_keeps_a_failure_and_raises_it_again() -> None:
    outcomes: list[tuple[Any, Any]] = []

    def _fail() -> dict[str, Any]:
        raise RuntimeError("gone")

    pending = PendingResult(ready=lambda: True, result=_fail, timeout_s=1.0)
    pending.add_done_callback(lambda result, error: outcomes.append((result, error)))

    for _ in range(2):
        with pytest.raises(RuntimeError, match="gone"):
            pending.resolve()
    assert len(outcomes) == 1
    assert outcomes[0][0] is None
    assert isinstance(outcomes[0][1], RuntimeError)


def test_a_parked_result_that_fails_with_a_command_error_keeps_its_code() -> None:
    clock, calls = _Clock(), []

    def _fail() -> dict[str, Any]:
        raise CommandExecutionError(code="INVALID_ARGUMENT", message="bad", hint="fix it")

    def _executor(name: str, args: dict[str, Any], meta: dict[str, Any]) -> Any:
        calls.append(name)
        return PendingResult(ready=lambda: False, result=_fail, timeout_s=1.0)

    server = _server(_executor, clock=clock)
    server.start()
    try:
        sock = _connect_and_send(server.server_address[1], _request_line("play", "transport_play"))
        _settle()
        server.poll()
        clock.now += 1.5
        server.poll()
        error = _read_response(sock)["error"]
        sock.close()
    finally:
        server.stop()

    assert error["code"] == "INVALID_ARGUMENT"
    assert error["message"] == "bad"
