from __future__ import annotations

import pytest
from pkm.adapters.base import (
    AdapterChunk,
    AdapterCitation,
    AdapterEntityLink,
    AdapterItem,
    SyncBatch,
)
from pkm.ingestion import IngestionPipeline
from pkm.store import PKMStore
from pkm.syncer import SyncOrchestrator


def test_ingestion_upsert_is_idempotent_with_checksums(tmp_path) -> None:
    store = PKMStore(tmp_path / "pkm.db")
    store.open()
    try:
        pipeline = IngestionPipeline(store)
        item = AdapterItem(
            external_id="incident-1",
            title="Incident 1",
            summary="Router outage retrospective",
            updated_at="2026-06-12T04:00:00Z",
            chunks=(
                AdapterChunk(
                    external_item_id="incident-1",
                    ordinal=0,
                    text="Router outage retrospective and follow-up actions.",
                    citations=(AdapterCitation(key="fixture://incident-1", locator="chunk:0"),),
                    entity_links=(AdapterEntityLink("topic", "router-outage", 1.0),),
                ),
            ),
        )
        first = pipeline.ingest_batch(
            source_key="fixture-src",
            adapter_kind="fixture",
            items=[item],
        )
        second = pipeline.ingest_batch(
            source_key="fixture-src",
            adapter_kind="fixture",
            items=[item],
        )
        assert first.inserted_items == 1
        assert first.chunks_written == 1
        assert first.citations_written == 1
        assert first.entity_links_written == 1
        assert second.unchanged_items == 1
        assert second.chunks_written == 0
        assert second.citations_written == 0
        assert second.entity_links_written == 0
        item_count = int(
            store.connection.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        )
        chunk_count = int(
            store.connection.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        )
        citation_count = int(
            store.connection.execute("SELECT COUNT(*) AS c FROM citations").fetchone()["c"]
        )
        entity_count = int(
            store.connection.execute("SELECT COUNT(*) AS c FROM entity_links").fetchone()["c"]
        )
        assert item_count == 1
        assert chunk_count == 1
        assert citation_count == 1
        assert entity_count == 1
    finally:
        store.close()


class _FlakyFixtureAdapter:
    source_key = "fixture-sync"
    adapter_kind = "fixture"

    def __init__(self) -> None:
        self._calls = 0

    async def pull(self, checkpoint, *, limit: int = 100) -> SyncBatch:
        self._calls += 1
        if self._calls == 1:
            return SyncBatch(
                items=[
                    AdapterItem(
                        external_id="sync-item-1",
                        title="Sync Item 1",
                        summary="Checkpoint should move forward.",
                        updated_at="2026-06-12T05:00:00Z",
                        chunks=(
                            AdapterChunk(
                                external_item_id="sync-item-1",
                                ordinal=0,
                                text="First successful sync payload.",
                                citations=(AdapterCitation("fixture://sync-item-1", "chunk:0"),),
                            ),
                        ),
                    )
                ],
                next_cursor="cursor-1",
                checkpoint_time="2026-06-12T05:00:00Z",
            )
        raise RuntimeError("simulated adapter failure")


@pytest.mark.asyncio
async def test_sync_orchestrator_updates_checkpoint_and_records_failures(tmp_path) -> None:
    store = PKMStore(tmp_path / "pkm.db")
    store.open()
    try:
        adapter = _FlakyFixtureAdapter()
        orchestrator = SyncOrchestrator(store, [adapter])
        first = await orchestrator.sync_source("fixture-sync")
        assert first.fetched_count == 1
        checkpoint = store.get_checkpoint("fixture-sync")
        assert checkpoint is not None
        assert checkpoint.cursor == "cursor-1"
        assert checkpoint.last_error is None

        with pytest.raises(RuntimeError, match="simulated adapter failure"):
            await orchestrator.sync_source("fixture-sync")
        failed_checkpoint = store.get_checkpoint("fixture-sync")
        assert failed_checkpoint is not None
        assert failed_checkpoint.last_error == "simulated adapter failure"
        assert failed_checkpoint.full_sync_required is True
    finally:
        store.close()
