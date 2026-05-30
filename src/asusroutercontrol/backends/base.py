"""Abstract base class for firmware backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from asusroutercontrol.models import (
    Device,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)


class BackendOperationUnsupported(NotImplementedError):
    """Raised when a backend does not support the requested operation.

    Callers should catch this to degrade gracefully rather than crashing,
    e.g. disabling write-path CLI commands when connected to a read-only backend.
    """

class FirmwareBackend(ABC):
    """Contract for all router firmware integrations.

    Implementations: MerlinBackend (asusrouter lib), FreshTomatoBackend (future).
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_connected_devices(self) -> list[Device]: ...

    @abstractmethod
    async def get_traffic_stats(self) -> TrafficSnapshot: ...

    @abstractmethod
    async def get_system_info(self) -> SystemInfo: ...

    @abstractmethod
    async def get_wan_status(self) -> WANStatus: ...

    @abstractmethod
    async def get_wifi_clients(self) -> list[WiFiClient]: ...

    @abstractmethod
    async def set_state(self, action: str, **kwargs) -> bool: ...

    @abstractmethod
    async def get_port_forwarding(self) -> list[PortRule]: ...

    @abstractmethod
    async def set_port_forwarding(self, rules: list[PortRule]) -> bool: ...
