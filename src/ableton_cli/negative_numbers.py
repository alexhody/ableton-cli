"""Accept negative numbers as positional values without a ``--`` separator.

Click reads any token that starts with ``-`` as an option, so ``track panning set -0.25``
fails with ``No such option: -0``. No option in this CLI looks like a number, so a
token such as ``-0.25`` can only be a value. When every other dash-prefixed token is a
known option, the command parses with ``ignore_unknown_options`` so click passes the
number through to its positional argument. Anything else (a typo such as
``--trak-index``) keeps click's normal "No such option" error.
"""

from __future__ import annotations

import re

import click
import typer
from typer.core import TyperCommand

_NEGATIVE_NUMBER = re.compile(r"^-(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def is_negative_number(token: str) -> bool:
    return _NEGATIVE_NUMBER.match(token) is not None


def _known_option_names(command: click.Command, ctx: click.Context) -> set[str]:
    names = set(command.get_help_option_names(ctx))
    for param in command.params:
        if isinstance(param, click.Option):
            names.update(param.opts)
            names.update(param.secondary_opts)
    return names


def _only_negative_numbers_look_unknown(
    command: click.Command, ctx: click.Context, args: list[str]
) -> bool:
    known = _known_option_names(command, ctx)
    found_number = False
    for token in args:
        if token == "--":
            break
        if is_negative_number(token):
            found_number = True
        elif token.startswith("-") and token != "-" and token.split("=", 1)[0] not in known:
            return False
    return found_number


class NegativeNumberCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if _only_negative_numbers_look_unknown(self, ctx, args):
            ctx.ignore_unknown_options = True
        return super().parse_args(ctx, args)


def accept_negative_number_arguments(app: typer.Typer) -> None:
    """Use :class:`NegativeNumberCommand` for every command that has no custom class."""
    for command_info in app.registered_commands:
        if command_info.cls in (None, TyperCommand):
            command_info.cls = NegativeNumberCommand
    for group_info in app.registered_groups:
        if isinstance(group_info.typer_instance, typer.Typer):
            accept_negative_number_arguments(group_info.typer_instance)
