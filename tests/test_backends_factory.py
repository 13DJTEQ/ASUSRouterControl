from __future__ import annotations

import pytest

from asusroutercontrol.backends.asuswrt import AsusWrtBackend
from asusroutercontrol.backends.factory import (
    BackendDeferredError,
    UnknownBackendError,
    create_backend,
)
from asusroutercontrol.backends.freshtomato import FreshTomatoBackend
from asusroutercontrol.config import Config


def test_create_backend_merlin() -> None:
    cfg = Config(router_backend="merlin")
    backend = create_backend(cfg, username="u", password="p")
    assert isinstance(backend, AsusWrtBackend)
    assert backend.flavor == "merlin"
    assert backend.supports_jffs is True
    assert backend.supports_entware is True


def test_create_backend_stock_alias() -> None:
    cfg = Config(router_backend="stock")
    backend = create_backend(cfg, username="u", password="p")
    assert isinstance(backend, AsusWrtBackend)
    assert backend.flavor == "stock"
    assert backend.supports_jffs is False
    assert backend.supports_entware is False


def test_create_backend_asuswrt_alias() -> None:
    cfg = Config(router_backend="asuswrt")
    backend = create_backend(cfg, username="u", password="p")
    assert isinstance(backend, AsusWrtBackend)
    assert backend.flavor == "stock"


def test_create_backend_freshtomato_deferred() -> None:
    cfg = Config(router_backend="freshtomato")
    with pytest.raises(BackendDeferredError, match="deferred"):
        create_backend(cfg, username="u", password="p")
    # Stub remains importable for later work.
    assert FreshTomatoBackend is not None


def test_create_backend_invalid_raises() -> None:
    cfg = Config(router_backend="invalid")
    with pytest.raises(UnknownBackendError):
        create_backend(cfg, username="u", password="p")


def test_stock_rejects_jffs_capability() -> None:
    from asusroutercontrol.backends.base import BackendOperationUnsupported

    backend = AsusWrtBackend(hostname="h", username="u", password="p", flavor="stock")
    with pytest.raises(BackendOperationUnsupported, match="jffs"):
        backend.require_capability("jffs")
    with pytest.raises(BackendOperationUnsupported, match="entware"):
        backend.require_capability("entware")


def test_merlin_allows_jffs_capability() -> None:
    backend = AsusWrtBackend(hostname="h", username="u", password="p", flavor="merlin")
    backend.require_capability("jffs")
    backend.require_capability("entware")
