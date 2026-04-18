"""Async SQLite persistence for router data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from asusroutercontrol._time import utcnow
from asusroutercontrol.models import (
    ClientLoad,
    ConfigEvent,
    ConfigSnapshot,
    Device,
    LatencyProbe,
    SpeedTestResult,
    SystemSnapshot,
    TrafficSnapshot,
    WiFiSnapshot,
)

log = logging.getLogger(__name__)
_MAX_ABSOLUTE_SPEED_BPS = 2_000_000_000
_SUSPECT_DOWNLOAD_BPS = 500_000_000
_SUSPECT_UPLOAD_BPS = 120_000_000
_MAX_REASONABLE_LATENCY_MS = 10_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY,
    ip TEXT,
    hostname TEXT,
    connection TEXT,
    band TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    is_known INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac TEXT NOT NULL,
    ip TEXT,
    hostname TEXT,
    connection TEXT,
    band TEXT,
    rssi INTEGER,
    tx_rate_mbps REAL,
    rx_rate_mbps REAL,
    seen_at TEXT NOT NULL,
    FOREIGN KEY (mac) REFERENCES devices(mac)
);

CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    rx_bytes INTEGER DEFAULT 0,
    tx_bytes INTEGER DEFAULT 0,
    rx_rate_bps REAL,
    tx_rate_bps REAL
);

CREATE TABLE IF NOT EXISTS speed_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    download_bps REAL,
    upload_bps REAL,
    ping_ms REAL,
    jitter_ms REAL,
    server_name TEXT,
    server_id TEXT,
    is_peak INTEGER DEFAULT 0,
    error TEXT,
    quality TEXT DEFAULT 'ok',
    source TEXT DEFAULT 'ookla',
    session_id TEXT DEFAULT '',
    provider_details_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS latency_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    target TEXT NOT NULL,
    min_ms REAL,
    avg_ms REAL,
    max_ms REAL,
    jitter_ms REAL,
    loss_pct REAL DEFAULT 0,
    samples INTEGER DEFAULT 0,
    quality TEXT DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS system_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cpu_pct REAL,
    ram_pct REAL,
    temp_c REAL,
    uptime_s INTEGER,
    conntrack_count INTEGER,
    conntrack_max INTEGER
);

CREATE TABLE IF NOT EXISTS wifi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    band TEXT NOT NULL,
    client_count INTEGER DEFAULT 0,
    avg_rssi REAL,
    min_rssi REAL,
    channel TEXT,
    noise_floor REAL,
    rx_bytes INTEGER,
    tx_bytes INTEGER,
    rx_rate_bps REAL,
    tx_rate_bps REAL
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'scheduled',
    nvram_json TEXT NOT NULL DEFAULT '{}',
    diff_summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS config_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    nvram_changes_json TEXT DEFAULT '{}',
    triggered_by TEXT DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_key TEXT NOT NULL UNIQUE,
    last_notified TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_traffic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    tx_rate_mbps REAL,
    rx_rate_mbps REAL,
    rssi INTEGER,
    load_pct REAL DEFAULT 0,
    FOREIGN KEY (mac) REFERENCES devices(mac)
);

CREATE TABLE IF NOT EXISTS device_perf_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    tx_rate_mbps REAL,
    rx_rate_mbps REAL,
    rssi INTEGER,
    load_pct REAL DEFAULT 0,
    band TEXT,
    FOREIGN KEY (mac) REFERENCES devices(mac)
);

CREATE INDEX IF NOT EXISTS idx_device_perf_mac_ts ON device_perf_history(mac, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_client_traffic_mac_ts ON client_traffic(mac, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_mac ON device_sessions(mac);
CREATE INDEX IF NOT EXISTS idx_sessions_seen ON device_sessions(seen_at);
CREATE INDEX IF NOT EXISTS idx_sessions_mac_seen ON device_sessions(mac, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_devices_first_seen ON devices(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_speed_ts ON speed_tests(timestamp);
CREATE INDEX IF NOT EXISTS idx_speed_source_ts ON speed_tests(source, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_latency_ts ON latency_probes(timestamp);
CREATE INDEX IF NOT EXISTS idx_latency_target_ts ON latency_probes(target, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_ts ON system_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_wifi_ts ON wifi_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_wifi_band_ts ON wifi_snapshots(band, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_config_snap_ts ON config_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_config_event_ts ON config_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_notification_key ON notification_log(rec_key);
"""


class DataStore:
    """Async SQLite store for router monitoring data."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()
        log.info("DataStore opened: %s", self._db_path)

    async def _migrate(self) -> None:
        """Add columns that may be missing from older databases."""
        db = self._db
        if not db:
            return
        migrations = [
            ("speed_tests", "source", "TEXT DEFAULT 'ookla'"),
            ("speed_tests", "session_id", "TEXT DEFAULT ''"),
            ("speed_tests", "provider_details_json", "TEXT DEFAULT '{}'"),
            ("speed_tests", "quality", "TEXT DEFAULT 'ok'"),
            ("latency_probes", "quality", "TEXT DEFAULT 'ok'"),
            ("wifi_snapshots", "rx_bytes", "INTEGER"),
            ("wifi_snapshots", "tx_bytes", "INTEGER"),
            ("wifi_snapshots", "rx_rate_bps", "REAL"),
            ("wifi_snapshots", "tx_rate_bps", "REAL"),
        ]
        for table, col, col_def in migrations:
            try:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"
                )
                log.info("Migrated: %s.%s", table, col)
            except Exception:
                pass  # Column already exists

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("DataStore not open")
        return self._db

    async def commit(self) -> None:
        db = self._conn()
        await db.commit()

    async def rollback(self) -> None:
        db = self._conn()
        try:
            await db.rollback()
        except Exception:
            # Safe no-op if no active transaction.
            pass

    @staticmethod
    def _is_unknown_label(value: str | None) -> bool:
        if value is None:
            return True
        normalized = str(value).strip().lower()
        return normalized in {"", "unknown", "none", "null"}

    @staticmethod
    def _classify_speed_quality(result: SpeedTestResult) -> str:
        if result.error:
            return "error"

        values = (
            result.download_bps,
            result.upload_bps,
            result.ping_ms,
            result.jitter_ms,
        )
        if any(v is not None and v < 0 for v in values):
            return "invalid"

        if (
            (result.download_bps or 0) > _MAX_ABSOLUTE_SPEED_BPS
            or (result.upload_bps or 0) > _MAX_ABSOLUTE_SPEED_BPS
            or (result.ping_ms or 0) > _MAX_REASONABLE_LATENCY_MS
        ):
            return "invalid"

        if (
            result.download_bps == 0
            or result.upload_bps == 0
            or (result.download_bps or 0) > _SUSPECT_DOWNLOAD_BPS
            or (result.upload_bps or 0) > _SUSPECT_UPLOAD_BPS
            or (result.ping_ms or 0) > 2_000
            or (result.jitter_ms or 0) > 1_000
        ):
            return "suspect"

        return "ok"

    @staticmethod
    def _classify_latency_quality(probe: LatencyProbe) -> str:
        if not probe.target:
            return "invalid"
        values = (probe.min_ms, probe.avg_ms, probe.max_ms, probe.jitter_ms)
        if any(v is not None and v < 0 for v in values):
            return "invalid"
        if probe.samples < 0 or probe.loss_pct < 0 or probe.loss_pct > 100:
            return "invalid"
        if (
            (probe.avg_ms or 0) > _MAX_REASONABLE_LATENCY_MS
            or (probe.jitter_ms or 0) > _MAX_REASONABLE_LATENCY_MS
            or probe.loss_pct > 90
            or ((probe.avg_ms or 0) == 0 and probe.samples > 0)
        ):
            return "suspect"
        return "ok"

    # --- Devices ---

    async def upsert_device(self, dev: Device, *, commit: bool = True) -> bool:
        """Insert or update device. Returns True if device is new."""
        db = self._conn()
        now = dev.last_seen or utcnow()
        now_str = now.isoformat()

        async with db.execute(
            "SELECT first_seen, connection, band FROM devices WHERE mac = ?",
            (dev.mac,),
        ) as cur:
            row = await cur.fetchone()

        is_new = row is None
        incoming_connection = (dev.connection.value or "").strip()
        if is_new:
            merged_connection = (
                incoming_connection if incoming_connection else "unknown"
            )
            if merged_connection == "wired":
                merged_band = None
            elif not self._is_unknown_label(dev.band):
                merged_band = dev.band
            elif not self._is_unknown_label(merged_connection):
                merged_band = merged_connection
            else:
                merged_band = dev.band
            await db.execute(
                "INSERT INTO devices (mac, ip, hostname, connection, band, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dev.mac,
                    dev.ip,
                    dev.hostname,
                    merged_connection,
                    merged_band,
                    now_str,
                    now_str,
                ),
            )
        else:
            existing_connection = (row["connection"] or "").strip()
            existing_band_raw = row["band"]
            existing_band = (
                str(existing_band_raw).strip() if existing_band_raw is not None else ""
            )

            if (
                self._is_unknown_label(incoming_connection)
                and not self._is_unknown_label(existing_connection)
            ):
                merged_connection = existing_connection
            else:
                merged_connection = incoming_connection or existing_connection or "unknown"

            if merged_connection == "wired":
                merged_band = None
            elif not self._is_unknown_label(dev.band):
                merged_band = dev.band
            elif not self._is_unknown_label(existing_band):
                merged_band = existing_band_raw
            elif not self._is_unknown_label(merged_connection):
                merged_band = merged_connection
            else:
                merged_band = dev.band
            await db.execute(
                "UPDATE devices SET ip=?, hostname=?, connection=?, band=?, last_seen=?"
                " WHERE mac=?",
                (
                    dev.ip,
                    dev.hostname,
                    merged_connection,
                    merged_band,
                    now_str,
                    dev.mac,
                ),
            )

        # Always record session
        await db.execute(
            "INSERT INTO device_sessions"
            " (mac, ip, hostname, connection, band, rssi, tx_rate_mbps, rx_rate_mbps, seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dev.mac, dev.ip, dev.hostname, merged_connection,
                merged_band, dev.rssi, dev.tx_rate_mbps, dev.rx_rate_mbps, now_str,
            ),
        )
        if commit:
            await db.commit()
        return is_new

    async def mark_known(self, mac: str, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute("UPDATE devices SET is_known = 1 WHERE mac = ?", (mac,))
        if commit:
            await db.commit()

    async def get_all_devices(self) -> list[dict]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM devices ORDER BY last_seen DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_device_sessions(
        self, mac: str, *, limit: int = 100
    ) -> list[dict]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM device_sessions WHERE mac = ? ORDER BY seen_at DESC LIMIT ?",
            (mac, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_unknown_devices(self) -> list[dict]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM devices WHERE is_known = 0 ORDER BY first_seen DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- Traffic ---

    async def insert_traffic(self, snap: TrafficSnapshot, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO traffic_snapshots"
            " (timestamp, rx_bytes, tx_bytes, rx_rate_bps, tx_rate_bps)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                snap.timestamp.isoformat(),
                snap.rx_bytes, snap.tx_bytes,
                snap.rx_rate_bps, snap.tx_rate_bps,
            ),
        )
        if commit:
            await db.commit()

    async def get_traffic_history(self, *, hours: int = 24, limit: int = 1000) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        async with db.execute(
            "SELECT * FROM traffic_snapshots WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (cutoff.isoformat(), limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- Speed Tests ---

    async def insert_speed_test(self, result: SpeedTestResult, *, commit: bool = True) -> None:
        db = self._conn()
        quality = self._classify_speed_quality(result)
        if quality == "invalid":
            log.warning(
                "Discarding invalid speed test row (source=%s, session_id=%s)",
                result.source,
                result.session_id,
            )
            return
        await db.execute(
            "INSERT INTO speed_tests"
            " (timestamp, download_bps, upload_bps, ping_ms, jitter_ms,"
            "  server_name, server_id, is_peak, error,"
            "  quality, source, session_id, provider_details_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.timestamp.isoformat(),
                result.download_bps, result.upload_bps,
                result.ping_ms, result.jitter_ms,
                result.server_name, result.server_id,
                int(result.is_peak), result.error,
                quality,
                result.source, result.session_id,
                result.provider_details_json,
            ),
        )
        if commit:
            await db.commit()

    async def get_speed_tests(
        self, *, days: int = 7, source: str | None = None
    ) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = "SELECT * FROM speed_tests WHERE timestamp >= ?"
        params: list = [cutoff.isoformat()]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_speed_metric_series(
        self,
        *,
        days: int = 7,
        metric: str = "download_bps",
        source: str | None = None,
    ) -> list[dict]:
        allowed = {"download_bps", "upload_bps", "ping_ms", "jitter_ms"}
        if metric not in allowed:
            raise ValueError(f"Unsupported speed metric: {metric}")
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = (
            f"SELECT timestamp, {metric}, quality FROM speed_tests WHERE timestamp >= ?"
            f" AND {metric} IS NOT NULL"
        )
        params: list = [cutoff.isoformat()]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_latency_metric_series(
        self,
        *,
        days: int = 7,
        metric: str = "avg_ms",
        target: str | None = None,
    ) -> list[dict]:
        allowed = {"min_ms", "avg_ms", "max_ms", "jitter_ms", "loss_pct"}
        if metric not in allowed:
            raise ValueError(f"Unsupported latency metric: {metric}")
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = (
            f"SELECT timestamp, target, {metric}, quality FROM latency_probes"
            " WHERE timestamp >= ?"
            f" AND {metric} IS NOT NULL"
        )
        params: list = [cutoff.isoformat()]
        if target:
            sql += " AND target = ?"
            params.append(target)
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_wifi_metric_series(
        self,
        *,
        days: int = 7,
        metric: str = "avg_rssi",
        band: str | None = None,
    ) -> list[dict]:
        allowed = {
            "client_count",
            "avg_rssi",
            "min_rssi",
            "noise_floor",
            "rx_rate_bps",
            "tx_rate_bps",
        }
        if metric not in allowed:
            raise ValueError(f"Unsupported wifi metric: {metric}")
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = f"SELECT timestamp, band, {metric} FROM wifi_snapshots WHERE timestamp >= ?"
        params: list = [cutoff.isoformat()]
        if band:
            sql += " AND band = ?"
            params.append(band)
        sql += f" AND {metric} IS NOT NULL"
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- Latency Probes ---

    async def insert_latency_probe(self, probe: LatencyProbe, *, commit: bool = True) -> None:
        db = self._conn()
        quality = self._classify_latency_quality(probe)
        if quality == "invalid":
            log.warning("Discarding invalid latency probe row for target=%s", probe.target)
            return
        await db.execute(
            "INSERT INTO latency_probes"
            " (timestamp, target, min_ms, avg_ms, max_ms, jitter_ms, loss_pct, samples, quality)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                probe.timestamp.isoformat(),
                probe.target, probe.min_ms, probe.avg_ms,
                probe.max_ms, probe.jitter_ms, probe.loss_pct, probe.samples, quality,
            ),
        )
        if commit:
            await db.commit()

    async def get_latency_probes(self, *, days: int = 7, target: str | None = None) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = "SELECT * FROM latency_probes WHERE timestamp >= ?"
        params: list = [cutoff.isoformat()]
        if target:
            sql += " AND target = ?"
            params.append(target)
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- System Snapshots ---

    async def insert_system_snapshot(self, snap: SystemSnapshot, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO system_snapshots"
            " (timestamp, cpu_pct, ram_pct, temp_c, uptime_s, conntrack_count, conntrack_max)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                snap.timestamp.isoformat(),
                snap.cpu_pct, snap.ram_pct, snap.temp_c,
                snap.uptime_s, snap.conntrack_count, snap.conntrack_max,
            ),
        )
        if commit:
            await db.commit()

    async def get_system_snapshots(self, *, days: int = 7) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        async with db.execute(
            "SELECT * FROM system_snapshots WHERE timestamp >= ?"
            " ORDER BY timestamp DESC",
            (cutoff.isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_system_metric_series(
        self, *, days: int = 7, metric: str = "ram_pct"
    ) -> list[dict]:
        allowed = {
            "cpu_pct",
            "ram_pct",
            "temp_c",
            "uptime_s",
            "conntrack_count",
            "conntrack_max",
        }
        if metric not in allowed:
            raise ValueError(f"Unsupported system metric: {metric}")
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        async with db.execute(
            f"SELECT timestamp, {metric} FROM system_snapshots WHERE timestamp >= ?"
            f" AND {metric} IS NOT NULL"
            " ORDER BY timestamp DESC",
            (cutoff.isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- WiFi Snapshots ---

    async def insert_wifi_snapshot(self, snap: WiFiSnapshot, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO wifi_snapshots"
            " (timestamp, band, client_count, avg_rssi, min_rssi, channel, noise_floor,"
            "  rx_bytes, tx_bytes, rx_rate_bps, tx_rate_bps)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snap.timestamp.isoformat(),
                snap.band, snap.client_count, snap.avg_rssi,
                snap.min_rssi, snap.channel, snap.noise_floor,
                snap.rx_bytes, snap.tx_bytes,
                snap.rx_rate_bps, snap.tx_rate_bps,
            ),
        )
        if commit:
            await db.commit()

    async def get_wifi_snapshots(self, *, days: int = 7, band: str | None = None) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        sql = "SELECT * FROM wifi_snapshots WHERE timestamp >= ?"
        params: list = [cutoff.isoformat()]
        if band:
            sql += " AND band = ?"
            params.append(band)
        sql += " ORDER BY timestamp DESC"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_latest_wifi_snapshot(self, band: str) -> dict | None:
        """Return the most recent wifi_snapshot for a given band."""
        db = self._conn()
        async with db.execute(
            "SELECT * FROM wifi_snapshots WHERE band = ? ORDER BY id DESC LIMIT 1",
            (band,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # --- Config Snapshots ---

    async def insert_config_snapshot(self, snap: ConfigSnapshot, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO config_snapshots (timestamp, source, nvram_json, diff_summary)"
            " VALUES (?, ?, ?, ?)",
            (snap.timestamp.isoformat(), snap.source, snap.nvram_json, snap.diff_summary),
        )
        if commit:
            await db.commit()

    async def get_config_snapshots(self, *, days: int = 90) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        async with db.execute(
            "SELECT * FROM config_snapshots WHERE timestamp >= ?"
            " ORDER BY timestamp DESC",
            (cutoff.isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_latest_config_snapshot(self) -> dict | None:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM config_snapshots ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # --- Config Events ---

    async def insert_config_event(self, event: ConfigEvent, *, commit: bool = True) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO config_events"
            " (timestamp, event_type, description, nvram_changes_json, triggered_by)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                event.timestamp.isoformat(), event.event_type,
                event.description, event.nvram_changes_json, event.triggered_by,
            ),
        )
        if commit:
            await db.commit()

    async def get_config_events(self, *, days: int = 90) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        async with db.execute(
            "SELECT * FROM config_events WHERE timestamp >= ?"
            " ORDER BY timestamp DESC",
            (cutoff.isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- Windowed Aggregates (for efficient analysis) ---

    async def get_avg_download_between(
        self, *, start_ts: str, end_ts: str
    ) -> tuple[float | None, int]:
        db = self._conn()
        async with db.execute(
            "SELECT AVG(download_bps) as avg_val, COUNT(download_bps) as sample_count"
            " FROM speed_tests"
            " WHERE timestamp >= ? AND timestamp <= ?"
            " AND download_bps IS NOT NULL"
            " AND quality IN ('ok', 'suspect')",
            (start_ts, end_ts),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            avg_val = row["avg_val"]
            count = int(row["sample_count"] or 0)
            return (float(avg_val), count) if avg_val is not None else (None, count)

    async def get_avg_latency_between(
        self, *, start_ts: str, end_ts: str, target: str = "gateway"
    ) -> tuple[float | None, int]:
        db = self._conn()
        async with db.execute(
            "SELECT AVG(avg_ms) as avg_val, COUNT(avg_ms) as sample_count"
            " FROM latency_probes"
            " WHERE timestamp >= ? AND timestamp <= ?"
            " AND target = ?"
            " AND avg_ms IS NOT NULL"
            " AND quality IN ('ok', 'suspect')",
            (start_ts, end_ts, target),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            avg_val = row["avg_val"]
            count = int(row["sample_count"] or 0)
            return (float(avg_val), count) if avg_val is not None else (None, count)

    async def get_avg_ram_between(
        self, *, start_ts: str, end_ts: str
    ) -> tuple[float | None, int]:
        db = self._conn()
        async with db.execute(
            "SELECT AVG(ram_pct) as avg_val, COUNT(ram_pct) as sample_count"
            " FROM system_snapshots"
            " WHERE timestamp >= ? AND timestamp <= ?"
            " AND ram_pct IS NOT NULL",
            (start_ts, end_ts),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            avg_val = row["avg_val"]
            count = int(row["sample_count"] or 0)
            return (float(avg_val), count) if avg_val is not None else (None, count)

    # --- Recommendation Notification Cooldowns ---

    async def get_notification_last_sent(self, rec_key: str) -> datetime | None:
        db = self._conn()
        async with db.execute(
            "SELECT last_notified FROM notification_log WHERE rec_key = ?",
            (rec_key,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            ts = row["last_notified"]
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return None

    async def set_notification_last_sent(
        self, rec_key: str, *, sent_at: datetime | None = None
    ) -> None:
        db = self._conn()
        sent = (sent_at or utcnow()).isoformat()
        await db.execute(
            "INSERT INTO notification_log (rec_key, last_notified) VALUES (?, ?)"
            " ON CONFLICT(rec_key) DO UPDATE SET last_notified=excluded.last_notified",
            (rec_key, sent),
        )
        await db.commit()

    # --- Data Retention ---

    async def prune_old_data(self, *, retention_days: int = 90) -> dict[str, int]:
        """Delete rows older than retention_days. Returns count deleted per table."""
        db = self._conn()
        cutoff_ts = (utcnow() - timedelta(days=max(1, retention_days))).isoformat()
        pruned: dict[str, int] = {}
        for table, col in [
            ("latency_probes", "timestamp"),
            ("system_snapshots", "timestamp"),
            ("wifi_snapshots", "timestamp"),
            ("device_sessions", "seen_at"),
            ("traffic_snapshots", "timestamp"),
            ("speed_tests", "timestamp"),
            ("config_snapshots", "timestamp"),
            ("config_events", "timestamp"),
            ("notification_log", "last_notified"),
            ("client_traffic", "timestamp"),
            ("device_perf_history", "timestamp"),
        ]:
            cur = await db.execute(
                f"DELETE FROM {table} WHERE {col} < ?", (cutoff_ts,)
            )
            pruned[table] = cur.rowcount
        await db.commit()
        return pruned

    # --- Client Traffic ---

    async def insert_client_load(
        self, load: ClientLoad, *, commit: bool = True
    ) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO client_traffic"
            " (mac, timestamp, tx_rate_mbps, rx_rate_mbps, rssi, load_pct)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                load.mac,
                load.timestamp.isoformat(),
                load.tx_rate_mbps,
                load.rx_rate_mbps,
                load.rssi,
                load.load_pct,
            ),
        )
        if commit:
            await db.commit()

    async def get_client_loads(self, *, hours: int = 1, limit: int = 500) -> list[dict]:
        """Return latest per-client load rows within the window.

        Prefers device_perf_history because it persists per-sample transport band.
        Falls back to client_traffic for legacy rows.
        """
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        signal_expr_perf = (
            "CASE WHEN dph.tx_rate_mbps IS NOT NULL OR dph.rx_rate_mbps IS NOT NULL "
            "OR dph.rssi IS NOT NULL "
            "THEN 1 ELSE 0 END"
        )
        signal_expr_traffic = (
            "CASE WHEN ct.tx_rate_mbps IS NOT NULL OR ct.rx_rate_mbps IS NOT NULL "
            "OR ct.rssi IS NOT NULL "
            "THEN 1 ELSE 0 END"
        )
        preferred_band_expr = (
            "CASE "
            "WHEN dph.band IS NOT NULL AND TRIM(dph.band) <> '' "
            "AND LOWER(TRIM(dph.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN dph.band "
            "WHEN d.connection = 'wired' THEN 'wired' "
            "WHEN d.band IS NOT NULL AND TRIM(d.band) <> '' "
            "AND LOWER(TRIM(d.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN d.band "
            "WHEN d.connection IS NOT NULL AND TRIM(d.connection) <> '' "
            "THEN d.connection "
            "ELSE NULL END"
        )
        fallback_band_expr = (
            "CASE "
            "WHEN d.connection = 'wired' THEN 'wired' "
            "WHEN d.band IS NOT NULL AND TRIM(d.band) <> '' "
            "AND LOWER(TRIM(d.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN d.band "
            "WHEN d.connection IS NOT NULL AND TRIM(d.connection) <> '' "
            "THEN d.connection "
            "ELSE NULL END"
        )
        async with db.execute(
            "SELECT dph.mac, dph.timestamp, dph.tx_rate_mbps, dph.rx_rate_mbps, "
            "dph.rssi, dph.load_pct, d.hostname, "
            f"{preferred_band_expr} AS band, "
            f"{signal_expr_perf} AS has_signal"
            " FROM device_perf_history dph"
            " LEFT JOIN devices d ON dph.mac = d.mac"
            " WHERE dph.timestamp >= ?"
            " ORDER BY has_signal DESC, dph.load_pct DESC, dph.timestamp DESC"
            " LIMIT ?",
            (cutoff.isoformat(), limit),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            return rows

        async with db.execute(
            "SELECT ct.*, d.hostname, "
            f"{fallback_band_expr} AS band, "
            f"{signal_expr_traffic} AS has_signal"
            " FROM client_traffic ct"
            " LEFT JOIN devices d ON ct.mac = d.mac"
            " WHERE ct.timestamp >= ?"
            " ORDER BY has_signal DESC, ct.load_pct DESC, ct.timestamp DESC"
            " LIMIT ?",
            (cutoff.isoformat(), limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_client_load_history(
        self, mac: str, *, hours: int = 24, limit: int = 500
    ) -> list[dict]:
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        preferred_band_expr = (
            "CASE "
            "WHEN dph.band IS NOT NULL AND TRIM(dph.band) <> '' "
            "AND LOWER(TRIM(dph.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN dph.band "
            "WHEN d.connection = 'wired' THEN 'wired' "
            "WHEN d.band IS NOT NULL AND TRIM(d.band) <> '' "
            "AND LOWER(TRIM(d.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN d.band "
            "WHEN d.connection IS NOT NULL AND TRIM(d.connection) <> '' "
            "THEN d.connection "
            "ELSE NULL END"
        )
        fallback_band_expr = (
            "CASE "
            "WHEN d.connection = 'wired' THEN 'wired' "
            "WHEN d.band IS NOT NULL AND TRIM(d.band) <> '' "
            "AND LOWER(TRIM(d.band)) NOT IN ('unknown', 'none', 'null') "
            "THEN d.band "
            "WHEN d.connection IS NOT NULL AND TRIM(d.connection) <> '' "
            "THEN d.connection "
            "ELSE NULL END"
        )
        async with db.execute(
            "SELECT dph.mac, dph.timestamp, dph.tx_rate_mbps, dph.rx_rate_mbps, "
            "dph.rssi, dph.load_pct, d.hostname, "
            f"{preferred_band_expr} AS band"
            " FROM device_perf_history dph"
            " LEFT JOIN devices d ON dph.mac = d.mac"
            " WHERE dph.mac = ? AND dph.timestamp >= ?"
            " ORDER BY dph.timestamp DESC LIMIT ?",
            (mac, cutoff.isoformat(), limit),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            return rows
        async with db.execute(
            "SELECT ct.mac, ct.timestamp, ct.tx_rate_mbps, ct.rx_rate_mbps, "
            "ct.rssi, ct.load_pct, d.hostname, "
            f"{fallback_band_expr} AS band"
            " FROM client_traffic ct"
            " LEFT JOIN devices d ON ct.mac = d.mac"
            " WHERE ct.mac = ? AND ct.timestamp >= ?"
            " ORDER BY ct.timestamp DESC LIMIT ?",
            (mac, cutoff.isoformat(), limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def insert_device_perf(
        self, load: ClientLoad, *, commit: bool = True
    ) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO device_perf_history"
            " (mac, timestamp, tx_rate_mbps, rx_rate_mbps, rssi, load_pct, band)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                load.mac,
                load.timestamp.isoformat(),
                load.tx_rate_mbps,
                load.rx_rate_mbps,
                load.rssi,
                load.load_pct,
                load.band,
            ),
        )
        if commit:
            await db.commit()

    async def get_device_perf_history(
        self, mac: str, *, days: int = 7, limit: int = 1000
    ) -> list[dict]:
        """Return device_perf_history rows for a given MAC."""
        db = self._conn()
        cutoff = utcnow() - timedelta(days=max(1, days))
        async with db.execute(
            "SELECT * FROM device_perf_history"
            " WHERE mac = ? AND timestamp >= ?"
            " ORDER BY timestamp DESC LIMIT ?",
            (mac, cutoff.isoformat(), limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_client_load_trends(self, *, hours: int = 1) -> dict[str, float | None]:
        """Return avg load_pct per MAC over the last N hours from device_perf_history.

        Returns {mac: avg_load_pct}.
        """
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        async with db.execute(
            "SELECT mac, AVG(load_pct) as avg_load"
            " FROM device_perf_history"
            " WHERE timestamp >= ? AND load_pct IS NOT NULL"
            " GROUP BY mac",
            (cutoff.isoformat(),),
        ) as cur:
            rows = await cur.fetchall()
        return {r["mac"]: float(r["avg_load"]) if r["avg_load"] is not None else None for r in rows}

    async def get_client_load_window_stats(self, *, hours: int = 1) -> dict:
        """Return aggregate diagnostics for client_traffic rows in a recent window."""
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        async with db.execute(
            "SELECT COUNT(*) AS samples,"
            " SUM(CASE WHEN tx_rate_mbps IS NOT NULL OR rx_rate_mbps IS NOT NULL"
            " OR rssi IS NOT NULL THEN 1 ELSE 0 END)"
            " AS signal_rows,"
            " SUM(CASE WHEN tx_rate_mbps IS NULL AND rx_rate_mbps IS NULL"
            " AND rssi IS NULL THEN 1 ELSE 0 END)"
            " AS placeholder_rows,"
            " MAX(load_pct) AS max_load_pct,"
            " AVG(load_pct) AS avg_load_pct"
            " FROM client_traffic"
            " WHERE timestamp >= ?",
            (cutoff.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}

    async def get_traffic_aggregates(self, *, hours: int = 24) -> dict:
        """Return aggregate traffic stats over the given window."""
        db = self._conn()
        cutoff = utcnow() - timedelta(hours=max(1, hours))
        async with db.execute(
            "SELECT COUNT(*) as samples,"
            " MAX(rx_bytes) - MIN(rx_bytes) as total_rx,"
            " MAX(tx_bytes) - MIN(tx_bytes) as total_tx,"
            " AVG(rx_rate_bps) as avg_rx_rate,"
            " AVG(tx_rate_bps) as avg_tx_rate,"
            " MAX(rx_rate_bps) as peak_rx_rate,"
            " MAX(tx_rate_bps) as peak_tx_rate"
            " FROM traffic_snapshots"
            " WHERE timestamp >= ?",
            (cutoff.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}
