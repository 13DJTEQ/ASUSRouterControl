"""Tests for the FastAPI web dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import ClientLoad, Device, SpeedTestResult, SystemSnapshot
from asusroutercontrol.web import create_app


@pytest.fixture
async def app_with_db(tmp_path):
    """Create a FastAPI app backed by a temp database."""
    db_path = tmp_path / "router.db"
    store = DataStore(db_path)
    await store.open()
    # Seed some data
    now = datetime.utcnow()
    await store.insert_speed_test(
        SpeedTestResult(
            timestamp=now - timedelta(minutes=10),
            download_bps=500_000_000,
            upload_bps=50_000_000,
            ping_ms=12.0,
            jitter_ms=2.0,
            source="ookla",
        ),
        commit=False,
    )
    await store.insert_system_snapshot(
        SystemSnapshot(
            timestamp=now - timedelta(minutes=5),
            cpu_pct=25.0,
            ram_pct=60.0,
            temp_c=55.0,
        ),
        commit=False,
    )
    await store.upsert_device(
        Device(
            mac="AA:BB:CC:DD:EE:01",
            ip="192.168.1.100",
            hostname="TestDevice",
            connection="wired",
        ),
        commit=False,
    )
    await store.insert_device_perf(
        ClientLoad(
            timestamp=now - timedelta(minutes=3),
            mac="AA:BB:CC:DD:EE:01",
            tx_rate_mbps=100.0,
            rx_rate_mbps=50.0,
            rssi=-45,
            load_pct=12.5,
        ),
        commit=False,
    )
    await store.commit()
    await store.close()

    app = create_app(db_path=db_path)
    return app


@pytest.mark.asyncio
async def test_dashboard_homepage(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/")
    assert resp.status_code == 200
    assert "ASUSRouterControl" in resp.text


@pytest.mark.asyncio
async def test_isp_performance_endpoint(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/isp-performance")
    assert resp.status_code == 200
    data = resp.json()
    assert "tests_total" in data
    assert data["tests_total"] == 1
    assert data["avg_download_mbps"] == 500.0


@pytest.mark.asyncio
async def test_client_load_endpoint(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/client-load")
    assert resp.status_code == 200
    data = resp.json()
    assert "clients" in data
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_devices_endpoint(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/devices")
    assert resp.status_code == 200
    data = resp.json()
    assert "devices" in data
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_health_endpoint(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert "grade" in data
    assert 0 <= data["score"] <= 100


@pytest.mark.asyncio
async def test_health_endpoint_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["score"] == 100.0
    assert data["grade"] == "A"


@pytest.mark.asyncio
async def test_isp_performance_hours_param(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/isp-performance?hours=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tests_total"] == 1
