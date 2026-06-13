from __future__ import annotations

from click.testing import CliRunner
from pkm.adapters.base import AdapterChunk, AdapterCitation, AdapterItem
from pkm.cli import cli
from pkm.ingestion import IngestionPipeline
from pkm.store import PKMStore


def _seed(db_path) -> None:
    store = PKMStore(db_path)
    store.open()
    try:
        pipeline = IngestionPipeline(store)
        pipeline.ingest_batch(
            source_key="cli-source",
            adapter_kind="fixture",
            items=[
                AdapterItem(
                    external_id="cli-item-1",
                    title="Router Incident Notes",
                    summary="Outage retrospective and owner list.",
                    updated_at="2026-06-12T06:00:00Z",
                    chunks=(
                        AdapterChunk(
                            external_item_id="cli-item-1",
                            ordinal=0,
                            text="Outage retrospective with mitigation tasks.",
                            citations=(AdapterCitation("fixture://cli-item-1", "chunk:0"),),
                        ),
                    ),
                ),
                AdapterItem(
                    external_id="cli-item-2",
                    title="Telemetry Follow-up",
                    summary="Search weighting validation notes.",
                    updated_at="2026-06-12T07:00:00Z",
                    chunks=(
                        AdapterChunk(
                            external_item_id="cli-item-2",
                            ordinal=0,
                            text="Telemetry weighting and retrieval baseline observations.",
                            citations=(AdapterCitation("fixture://cli-item-2", "chunk:0"),),
                        ),
                    ),
                ),
            ],
        )
    finally:
        store.close()


def test_cli_retrieval_outputs_are_citation_backed(tmp_path) -> None:
    db_path = tmp_path / "pkm.db"
    _seed(db_path)
    runner = CliRunner()

    search_result = runner.invoke(
        cli,
        ["search", "retrospective", "--db-path", str(db_path), "--limit", "5"],
    )
    assert search_result.exit_code == 0
    assert "citations:" in search_result.output
    assert "[cli-source]" in search_result.output

    brief_result = runner.invoke(
        cli,
        ["brief", "retrospective", "--db-path", str(db_path), "--window", "30d", "--limit", "3"],
    )
    assert brief_result.exit_code == 0
    assert "Brief:" in brief_result.output
    assert "Citations:" in brief_result.output

    timeline_result = runner.invoke(
        cli,
        ["timeline", "--db-path", str(db_path), "--limit", "5"],
    )
    assert timeline_result.exit_code == 0
    assert "citations:" in timeline_result.output
    assert "[cli-source]" in timeline_result.output
