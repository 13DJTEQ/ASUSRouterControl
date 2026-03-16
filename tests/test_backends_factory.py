from __future__ import annotations

import pytest

from asusroutercontrol.backends.factory import create_backend
from asusroutercontrol.backends.freshtomato import FreshTomatoBackend
from asusroutercontrol.backends.merlin import MerlinBackend
from asusroutercontrol.config import Config


def test_create_backend_merlin() -> None:
    cfg = Config(router_backend="merlin")
    backend = create_backend(cfg, username="u", password="p")
    assert isinstance(backend, MerlinBackend)


def test_create_backend_freshtomato() -> None:
    cfg = Config(router_backend="freshtomato")
    backend = create_backend(cfg, username="u", password="p")
    assert isinstance(backend, FreshTomatoBackend)


def test_create_backend_invalid_raises() -> None:
    cfg = Config(router_backend="invalid")
    with pytest.raises(ValueError):
        create_backend(cfg, username="u", password="p")
