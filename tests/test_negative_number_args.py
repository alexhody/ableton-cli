from __future__ import annotations

import json

import pytest


class _PanningClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, float]] = []

    def track_panning_set(self, track_ref, value: float):  # noqa: ANN001, ANN201
        self.calls.append((track_ref, value))
        return {"track": 0, "panning": value}


@pytest.fixture
def panning_client(monkeypatch) -> _PanningClient:
    from ableton_cli.commands import track

    client = _PanningClient()
    monkeypatch.setattr(track, "get_client", lambda ctx: client)
    return client


@pytest.mark.parametrize(
    "argv",
    [
        ["track", "panning", "set", "-0.25", "--track-index", "0"],
        ["track", "panning", "set", "--track-index", "0", "-0.25"],
        ["track", "panning", "set", "--track-index", "0", "--", "-0.25"],
        ["track", "panning", "set", "-.25", "--track-index", "0"],
        ["track", "panning", "set", "-2.5e-1", "--track-index", "0"],
    ],
)
def test_negative_positional_number_does_not_need_double_dash(
    runner, cli_app, panning_client: _PanningClient, argv: list[str]
) -> None:
    result = runner.invoke(cli_app, ["--output", "json", *argv])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["result"]["panning"] == -0.25
    assert [value for _, value in panning_client.calls] == [-0.25]


def test_negative_number_still_reaches_validation(runner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--output", "json", "transport", "position", "set", "-1"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_ARGUMENT"


def test_negative_number_as_option_value_is_unchanged(runner, cli_app, monkeypatch) -> None:
    from ableton_cli.commands import scenes

    calls: list[int] = []

    class _Client:
        def create_scene(self, index: int) -> dict[str, object]:
            calls.append(index)
            return {"index": index}

    monkeypatch.setattr(scenes, "get_client", lambda ctx: _Client())

    result = runner.invoke(cli_app, ["--output", "json", "scenes", "create", "--index", "-1"])

    assert result.exit_code == 0, result.output
    assert calls == [-1]


@pytest.mark.parametrize("typo", ["--trak-index", "-x"])
def test_unknown_options_are_still_rejected_next_to_negative_numbers(
    runner, cli_app, panning_client: _PanningClient, typo: str
) -> None:
    result = runner.invoke(
        cli_app,
        ["--output", "json", "track", "panning", "set", "-0.25", typo, "0"],
    )

    assert result.exit_code == 2
    assert "No such option" in result.output
    assert panning_client.calls == []


def test_dash_prefixed_text_is_not_treated_as_a_number(runner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--output", "json", "track", "panning", "set", "-abc"])

    assert result.exit_code == 2
    assert "No such option" in result.output
