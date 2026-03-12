"""RouterControlService — async orchestrator with polling loop."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.integrations.soundshield import export_soundshield_json

log = logging.getLogger(__name__)


class RouterControlService:
    """Polls router at a configurable interval, persists data, and exports integrations."""

    def __init__(
        self,
        backend: FirmwareBackend,
        datastore: DataStore,
        *,
        interval: int = 60,
        soundshield_path: Path | None = None,
        on_new_device=None,
    ) -> None:
        self._backend = backend
        self._store = datastore
        self._interval = interval
        self._ss_path = soundshield_path
        self._on_new_device = on_new_device
        self._running = False

    async def poll_once(self) -> dict:
        """Single poll cycle. Returns summary dict."""
        new_devices: list[str] = []

        devices = await self._backend.get_connected_devices()
        for dev in devices:
            is_new = await self._store.upsert_device(dev)
            if is_new:
                new_devices.append(f"{dev.hostname or dev.mac} ({dev.ip})")
                if self._on_new_device:
                    self._on_new_device(dev)

        traffic = await self._backend.get_traffic_stats()
        await self._store.insert_traffic(traffic)

        wan = await self._backend.get_wan_status()

        # SoundShield export
        if self._ss_path:
            await export_soundshield_json(devices, wan, self._ss_path)

        return {
            "devices": len(devices),
            "new_devices": new_devices,
            "rx_rate": traffic.rx_rate_bps,
            "tx_rate": traffic.tx_rate_bps,
            "wan_status": wan.status,
        }

    async def run(self) -> None:
        """Continuous polling loop."""
        self._running = True
        log.info("Monitor started (interval=%ds)", self._interval)
        try:
            while self._running:
                try:
                    summary = await self.poll_once()
                    log.info(
                        "Poll: %d devices, WAN=%s, RX=%.0f bps, TX=%.0f bps",
                        summary["devices"],
                        summary["wan_status"],
                        summary["rx_rate"] or 0,
                        summary["tx_rate"] or 0,
                    )
                    if summary["new_devices"]:
                        log.warning("New devices: %s", ", ".join(summary["new_devices"]))
                except Exception:
                    log.exception("Poll cycle failed")
                await asyncio.sleep(self._interval)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
