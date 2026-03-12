"""FreshTomato firmware backend — stub for future implementation."""

from __future__ import annotations

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.models import (
    Device,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)


class FreshTomatoBackend(FirmwareBackend):
    """Placeholder for FreshTomato firmware support.

    Will use HTTP/SSH against Tomato's web API.
    Not yet implemented — raise NotImplementedError for all methods.
    """

    async def connect(self) -> None:
        raise NotImplementedError("FreshTomato backend not yet implemented")

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def get_connected_devices(self) -> list[Device]:
        raise NotImplementedError

    async def get_traffic_stats(self) -> TrafficSnapshot:
        raise NotImplementedError

    async def get_system_info(self) -> SystemInfo:
        raise NotImplementedError

    async def get_wan_status(self) -> WANStatus:
        raise NotImplementedError

    async def get_wifi_clients(self) -> list[WiFiClient]:
        raise NotImplementedError

    async def set_state(self, action: str, **kwargs) -> bool:
        raise NotImplementedError

    async def get_port_forwarding(self) -> list[PortRule]:
        raise NotImplementedError

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        raise NotImplementedError
