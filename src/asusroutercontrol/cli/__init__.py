"""CLI package for ASUSRouterControl.

This package provides modular CLI command groups for the `asusrouter` command.
"""

from __future__ import annotations

import asyncio
import functools
import sys
from typing import TYPE_CHECKING, Callable, ParamSpec, TypeVar

import click
from rich.console import Console

from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import get_router_credentials
from asusroutercontrol.datastore import DataStore

if TYPE_CHECKING:
    from asusroutercontrol.backends.base import FirmwareBackend

__all__ = [
    "async_command",
    "console",
    "get_backend",
    "get_datastore",
    "run_with_backend",
]

console = Console()

P = ParamSpec("P")
T = TypeVar("T")


def async_command(fn: Callable[P, T]) -> Callable[P, T]:
    """Decorator that wraps an async Click command handler.

    Usage:
        @cli.command()
        @async_command
        async def my_command():
            await do_something()

    The decorated function can be a regular async function. The decorator
    handles asyncio.run() automatically.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def get_backend() -> "FirmwareBackend":
    """Create and return a configured firmware backend instance.

    Raises:
        click.ClickException: If credentials are not configured.
        ValueError: If the backend configuration is invalid.
    """
    from asusroutercontrol.backends.factory import create_backend

    cfg = load_config()
    username, password = get_router_credentials()
    if not username or not password:
        console.print(
            "[red]Router credentials not configured.[/red] Run: [bold]asusrouter setup[/bold]"
        )
        sys.exit(1)
    try:
        return create_backend(cfg, username=username, password=password)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


async def get_datastore() -> DataStore:
    """Create and open a DataStore instance.

    Caller is responsible for calling `await store.close()` when done.
    """
    cfg = load_config()
    store = DataStore(cfg.data_dir / "router.db")
    await store.open()
    return store


async def run_with_backend(coro_factory: Callable[["FirmwareBackend"], T]) -> T:
    """Connect to backend, run coroutine, disconnect.

    Args:
        coro_factory: Async function that takes a backend and returns a result.

    Raises:
        click.ClickException: If the backend operation is unsupported.
    """
    from asusroutercontrol.backends.base import BackendOperationUnsupported

    backend = get_backend()
    try:
        await backend.connect()
        try:
            return await coro_factory(backend)
        except BackendOperationUnsupported as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await backend.disconnect()
