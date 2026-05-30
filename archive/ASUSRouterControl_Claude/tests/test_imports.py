"""Import smoke tests — verify every module is importable.

These tests catch missing files, circular imports, and obvious syntax errors
without requiring a live router, SSH access, or macOS Keychain.
"""

import importlib

import pytest

# All modules that must be importable without external I/O.
# (menubar is excluded: it imports PyObjC which is macOS-only and optional)
_MODULES = [
    "asusroutercontrol",
    "asusroutercontrol.models",
    "asusroutercontrol.config",
    "asusroutercontrol.credentials",
    "asusroutercontrol._time",
    "asusroutercontrol.backends",
    "asusroutercontrol.backends.base",
    "asusroutercontrol.backends.factory",
    "asusroutercontrol.backends.merlin",
    "asusroutercontrol.backends.freshtomato",
    "asusroutercontrol.datastore",
    "asusroutercontrol.dhcp_reservations",
    "asusroutercontrol.ssh",
    "asusroutercontrol.probes",
    "asusroutercontrol.speedtest",
    "asusroutercontrol.speedtest_providers",
    "asusroutercontrol.analyzer",
    "asusroutercontrol.optimizer",
    "asusroutercontrol.executor",
    "asusroutercontrol.rollout",
    "asusroutercontrol.reporting",
    "asusroutercontrol.scheduler",
    "asusroutercontrol.notifications",
    "asusroutercontrol.incident",
    "asusroutercontrol.benchmark",
    "asusroutercontrol.dhcp_reservations",
    "asusroutercontrol.service",
    "asusroutercontrol.integrations",
    "asusroutercontrol.integrations.soundshield",
    "asusroutercontrol.analysis",
    "asusroutercontrol.analysis.clients",
    "asusroutercontrol.analysis.dashboard",
    "asusroutercontrol.analysis.devices",
    "asusroutercontrol.analysis.security",
    "asusroutercontrol.analysis.traffic",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_importable(module: str) -> None:
    """Each listed module must import without raising."""
    importlib.import_module(module)


def test_factory_exports_create_backend() -> None:
    """factory.create_backend must exist and be callable."""
    from asusroutercontrol.backends.factory import create_backend

    assert callable(create_backend)


def test_backend_operation_unsupported_is_exported() -> None:
    """BackendOperationUnsupported must be accessible from the backends package."""
    from asusroutercontrol.backends import BackendOperationUnsupported

    assert issubclass(BackendOperationUnsupported, NotImplementedError)


def test_unknown_backend_error_raised() -> None:
    """create_backend raises UnknownBackendError for unknown backend names."""
    from asusroutercontrol.backends.factory import UnknownBackendError, create_backend
    from asusroutercontrol.config import Config

    cfg = Config(router_backend="doesnotexist")
    with pytest.raises(UnknownBackendError):
        create_backend(cfg, username="u", password="p")


def test_factory_returns_merlin_for_merlin_backend() -> None:
    """create_backend('merlin') returns a MerlinBackend instance."""
    from asusroutercontrol.backends.factory import create_backend
    from asusroutercontrol.backends.merlin import MerlinBackend
    from asusroutercontrol.config import Config

    cfg = Config(router_backend="merlin")
    backend = create_backend(cfg, username="admin", password="secret")
    assert isinstance(backend, MerlinBackend)


def test_factory_returns_freshtomato_for_freshtomato_backend() -> None:
    """create_backend('freshtomato') returns a FreshTomatoBackend instance."""
    from asusroutercontrol.backends.factory import create_backend
    from asusroutercontrol.backends.freshtomato import FreshTomatoBackend
    from asusroutercontrol.config import Config

    cfg = Config(router_backend="freshtomato")
    backend = create_backend(cfg, username="admin", password="secret")
    assert isinstance(backend, FreshTomatoBackend)
