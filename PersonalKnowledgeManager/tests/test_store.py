from __future__ import annotations

from pathlib import Path

from pkm.store import PKMStore


def test_open_creates_schema_and_fts_search(tmp_path: Path) -> None:
    store = PKMStore(tmp_path / "pkm.db")
    store.open()
    try:
        objects = {
            row["name"]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view', 'trigger')"
            ).fetchall()
        }
        assert {
            "sources",
            "items",
            "chunks",
            "chunks_fts",
            "citations",
            "sync_state",
            "entity_links",
            "chunks_ai",
            "chunks_ad",
            "chunks_au",
        }.issubset(objects)
        assert store.schema_version() == 1

        source_id = store.register_source("mail-primary", "imap", display_name="Mail")
        store.connection.execute(
            """
            INSERT INTO items(source_id, external_id, title, raw_payload_json)
            VALUES(?, ?, ?, ?)
            """,
            (source_id, "item-001", "Router Incident", "{}"),
        )
        item_id = int(
            store.connection.execute(
                "SELECT id FROM items WHERE source_id = ? AND external_id = ?",
                (source_id, "item-001"),
            ).fetchone()["id"]
        )
        store.connection.execute(
            "INSERT INTO chunks(item_id, ordinal, text, token_count) VALUES(?, ?, ?, ?)",
            (item_id, 0, "Router outage retrospective and follow-up actions.", 6),
        )
        store.connection.commit()

        results = store.search_chunks("retrospective")
        assert len(results) == 1
        assert results[0]["source_key"] == "mail-primary"
        assert results[0]["chunk_id"] > 0
    finally:
        store.close()


def test_checkpoint_round_trip_and_failure(tmp_path: Path) -> None:
    store = PKMStore(tmp_path / "pkm.db")
    store.open()
    try:
        store.register_source("notion-team", "notion")
        initial = store.get_checkpoint("notion-team")
        assert initial is not None
        assert initial.cursor is None
        assert initial.full_sync_required is True

        saved = store.save_checkpoint(
            "notion-team",
            cursor="cursor-001",
            checkpoint_time="2026-06-13T02:40:00Z",
            metadata={"items": 4},
            full_sync_required=False,
        )
        assert saved.cursor == "cursor-001"
        assert saved.checkpoint_time == "2026-06-13T02:40:00Z"
        assert saved.full_sync_required is False
        assert saved.last_success_at is not None
        assert saved.last_error is None
        assert saved.metadata == {"items": 4}

        failed = store.record_sync_failure(
            "notion-team",
            "network timeout",
            full_sync_required=True,
        )
        assert failed.cursor == "cursor-001"
        assert failed.last_error == "network timeout"
        assert failed.last_attempt_at is not None
        assert failed.full_sync_required is True
    finally:
        store.close()
