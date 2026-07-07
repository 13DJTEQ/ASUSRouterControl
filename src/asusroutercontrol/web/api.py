"""REST API endpoints for router telemetry."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from asusroutercontrol.datastore import DataStore

router = APIRouter()


async def _get_store(request: Request) -> DataStore:
    """Open a DataStore from app state."""
    db_path = request.app.state.db_path
    store = DataStore(db_path)
    await store.open()
    return store


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Dashboard homepage."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/isp-performance")
async def isp_performance(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
):
    """ISP speed test data."""
    store = await _get_store(request)
    try:
        from asusroutercontrol.analysis.dashboard import build_isp_client_dashboard

        data = await build_isp_client_dashboard(store, hours=hours, clients=0, timeline_points=0)
        return data["isp_performance"]
    finally:
        await store.close()


@router.get("/client-load")
async def client_load(
    request: Request,
    hours: int = Query(default=1, ge=1, le=24),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Client device load data."""
    store = await _get_store(request)
    try:
        rows = await store.get_client_loads(hours=hours, limit=limit)
        return {"clients": rows, "count": len(rows)}
    finally:
        await store.close()


@router.get("/devices")
async def devices(request: Request):
    """Connected devices."""
    store = await _get_store(request)
    try:
        all_devs = await store.get_all_devices()
        return {"devices": all_devs, "count": len(all_devs)}
    finally:
        await store.close()


@router.get("/health")
async def health(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
):
    """Router health score based on system, speed, and latency data."""
    store = await _get_store(request)
    try:
        sys_rows = await store.get_system_snapshots(days=max(1, hours // 24))
        speed_rows = await store.get_speed_tests(days=max(1, hours // 24))
        latency_rows = await store.get_latency_probes(days=max(1, hours // 24))

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        recent_sys = [
            r for r in sys_rows
            if r.get("timestamp") and datetime.fromisoformat(r["timestamp"]) >= cutoff
        ]
        recent_speed = [
            r for r in speed_rows
            if r.get("timestamp") and datetime.fromisoformat(r["timestamp"]) >= cutoff
        ]
        recent_latency = [
            r for r in latency_rows
            if r.get("timestamp") and datetime.fromisoformat(r["timestamp"]) >= cutoff
        ]

        cpu_vals = [r["cpu_pct"] for r in recent_sys if r.get("cpu_pct") is not None]
        ram_vals = [r["ram_pct"] for r in recent_sys if r.get("ram_pct") is not None]
        temp_vals = [r["temp_c"] for r in recent_sys if r.get("temp_c") is not None]

        dl_vals = [
            r["download_bps"] / 1_000_000
            for r in recent_speed
            if r.get("download_bps") is not None
        ]
        ping_vals = [
            r["ping_ms"]
            for r in recent_latency
            if r.get("ping_ms") is not None
        ]

        score = 100.0
        details = {}

        if cpu_vals:
            avg_cpu = mean(cpu_vals)
            if avg_cpu > 90:
                score -= 25
            elif avg_cpu > 70:
                score -= 10
            details["avg_cpu_pct"] = round(avg_cpu, 1)

        if ram_vals:
            avg_ram = mean(ram_vals)
            if avg_ram > 90:
                score -= 20
            elif avg_ram > 75:
                score -= 10
            details["avg_ram_pct"] = round(avg_ram, 1)

        if temp_vals:
            avg_temp = mean(temp_vals)
            if avg_temp > 85:
                score -= 15
            elif avg_temp > 70:
                score -= 5
            details["avg_temp_c"] = round(avg_temp, 1)

        if dl_vals:
            avg_dl = mean(dl_vals)
            if avg_dl < 10:
                score -= 20
            elif avg_dl < 50:
                score -= 10
            details["avg_download_mbps"] = round(avg_dl, 2)

        if ping_vals:
            avg_ping = mean(ping_vals)
            if avg_ping > 100:
                score -= 15
            elif avg_ping > 50:
                score -= 5
            details["avg_ping_ms"] = round(avg_ping, 2)

        score = max(0.0, min(100.0, score))
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 50 else "F"

        return {
            "score": round(score, 1),
            "grade": grade,
            "window_hours": hours,
            "samples": {
                "system": len(recent_sys),
                "speed_tests": len(recent_speed),
                "latency_probes": len(recent_latency),
            },
            "details": details,
        }
    finally:
        await store.close()
