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


class BackendOperationUnsupported(RuntimeError):
    """Raised when a backend does not support a requested operation."""

    def __init__(self, backend: str, operation: str, reason: str | None = None) -> None:
        message = f"{backend} does not support operation `{operation}`"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.backend = backend
        self.operation = operation
        self.reason = reason


class FirmwareBackend(ABC):
    """Contract for all router firmware integrations.

    Implementations: MerlinBackend (asusrouter lib), FreshTomatoBackend (future).
    """

    def backend_name(self) -> str:
        return type(self).__name__

    def supported_operations(self) -> set[str]:
        return {
            "read.devices",
            "read.traffic",
            "read.system",
            "read.wan",
            "read.wifi_clients",
            "read.port_forwarding",
            "write.state",
            "write.port_forwarding",
        }

    def supports_operation(self, operation: str) -> bool:
        return operation in self.supported_operations()

    def require_operation(self, operation: str, reason: str | None = None) -> None:
        if not self.supports_operation(operation):
            raise BackendOperationUnsupported(self.backend_name(), operation, reason)

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
