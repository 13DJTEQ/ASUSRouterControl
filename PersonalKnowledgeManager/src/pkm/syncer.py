"""Sync orchestration for adapters and ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pkm.adapters.base import PKMSourceAdapter
from pkm.ingestion import IngestionPipeline, IngestionStats
from pkm.store import PKMStore
from pkm.utils import utcnow_iso


@dataclass(frozen=True, slots=True)
class SyncResult:
    source_key: str
    fetched_count: int
    next_cursor: str | None
    checkpoint_time: str | None
    stats: IngestionStats


class SyncOrchestrator:
    """Coordinates adapter pull, ingest upsert, and checkpoint lifecycle updates."""

    def __init__(self, store: PKMStore, adapters: Iterable[PKMSourceAdapter] = ()) -> None:
        self._store = store
        self._adapters: dict[str, PKMSourceAdapter] = {}
        for adapter in adapters:
            self.register_adapter(adapter)

    def register_adapter(self, adapter: PKMSourceAdapter) -> None:
        self._adapters[adapter.source_key] = adapter

    async def sync_source(self, source_key: str, *, limit: int = 100) -> SyncResult:
        adapter = self._adapters[source_key]
        self._store.register_source(source_key, adapter.adapter_kind)
        checkpoint = self._store.get_checkpoint(source_key)
        try:
            batch = await adapter.pull(checkpoint, limit=limit)
            pipeline = IngestionPipeline(self._store)
            stats = pipeline.ingest_batch(
                source_key=source_key,
                adapter_kind=adapter.adapter_kind,
                items=batch.items,
            )
            next_cursor = (
                batch.next_cursor
                if batch.next_cursor is not None
                else checkpoint.cursor if checkpoint else None
            )
            checkpoint_time = batch.checkpoint_time or utcnow_iso()
            metadata = {
                "fetched_count": len(batch.items),
                "ingested_items": stats.ingested_items,
                "inserted_items": stats.inserted_items,
                "updated_items": stats.updated_items,
                "unchanged_items": stats.unchanged_items,
                **dict(batch.metadata),
            }
            self._store.save_checkpoint(
                source_key,
                cursor=next_cursor,
                checkpoint_time=checkpoint_time,
                metadata=metadata,
                full_sync_required=batch.full_sync_required,
            )
            return SyncResult(
                source_key=source_key,
                fetched_count=len(batch.items),
                next_cursor=next_cursor,
                checkpoint_time=checkpoint_time,
                stats=stats,
            )
        except Exception as exc:
            self._store.record_sync_failure(source_key, str(exc), full_sync_required=True)
            raise
