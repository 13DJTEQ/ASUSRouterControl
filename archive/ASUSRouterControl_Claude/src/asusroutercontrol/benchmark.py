"""Deterministic local benchmark helpers for datastore/runtime baselining."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Awaitable, Callable

from asusroutercontrol.datastore import DataStore

QueryOperation = Callable[[], Awaitable[list[dict]]]


def _percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    rank = (len(samples) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(samples) - 1)
    weight = rank - lower
    return samples[lower] + (samples[upper] - samples[lower]) * weight


def _summarize_ms(samples_ms: list[float]) -> dict[str, float | int]:
    if not samples_ms:
        return {
            "count": 0,
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "mean_ms": 0.0,
            "max_ms": 0.0,
            "ops_per_sec": 0.0,
        }

    ordered = sorted(samples_ms)
    mean_ms = sum(ordered) / len(ordered)
    total_s = sum(ordered) / 1000.0
    ops_per_sec = (len(ordered) / total_s) if total_s > 0 else 0.0
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(_percentile(ordered, 0.50), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "mean_ms": round(mean_ms, 3),
        "max_ms": round(ordered[-1], 3),
        "ops_per_sec": round(ops_per_sec, 3),
    }


async def _time_query(operation: QueryOperation) -> tuple[float, int]:
    start = perf_counter()
    rows = await operation()
    elapsed_ms = (perf_counter() - start) * 1000.0
    return elapsed_ms, len(rows)


async def run_datastore_benchmark(
    store: DataStore,
    *,
    iterations: int = 25,
    days: int = 7,
) -> dict[str, object]:
    """Run deterministic local datastore timings and return structured metrics."""
    run_count = max(1, iterations)
    window_days = max(1, days)

    query_samples: dict[str, list[float]] = {
        "speed_tests": [],
        "latency_probes": [],
        "wifi_snapshots": [],
        "devices": [],
    }
    sample_sizes: dict[str, int] = {}

    query_ops: dict[str, QueryOperation] = {
        "speed_tests": lambda: store.get_speed_tests(days=window_days),
        "latency_probes": lambda: store.get_latency_probes(days=window_days),
        "wifi_snapshots": lambda: store.get_wifi_snapshots(days=window_days),
        "devices": store.get_all_devices,
    }

    for _ in range(run_count):
        for key, operation in query_ops.items():
            elapsed_ms, rows = await _time_query(operation)
            query_samples[key].append(elapsed_ms)
            sample_sizes[f"{key}_rows"] = rows

    db = store._conn()
    await db.execute(
        "CREATE TEMP TABLE IF NOT EXISTS benchmark_runtime (id INTEGER PRIMARY KEY, payload TEXT)"
    )
    await store.commit()

    write_insert_samples: list[float] = []
    write_commit_samples: list[float] = []
    for idx in range(run_count):
        insert_start = perf_counter()
        await db.execute(
            "INSERT INTO benchmark_runtime (payload) VALUES (?)",
            (f"sample-{idx}",),
        )
        write_insert_samples.append((perf_counter() - insert_start) * 1000.0)

        commit_start = perf_counter()
        await store.commit()
        write_commit_samples.append((perf_counter() - commit_start) * 1000.0)

    await db.execute("DROP TABLE IF EXISTS benchmark_runtime")
    await store.commit()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations": run_count,
        "days": window_days,
        "sample_sizes": sample_sizes,
        "metrics": {
            "query_speed_tests_ms": _summarize_ms(query_samples["speed_tests"]),
            "query_latency_probes_ms": _summarize_ms(query_samples["latency_probes"]),
            "query_wifi_snapshots_ms": _summarize_ms(query_samples["wifi_snapshots"]),
            "query_devices_ms": _summarize_ms(query_samples["devices"]),
            "write_temp_insert_ms": _summarize_ms(write_insert_samples),
            "write_commit_ms": _summarize_ms(write_commit_samples),
        },
    }
