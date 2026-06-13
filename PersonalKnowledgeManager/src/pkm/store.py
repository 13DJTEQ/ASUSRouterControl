"""SQLite schema, migrations, and checkpoint primitives for PKM."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from pkm.checkpoints import SyncCheckpoint
from pkm.utils import utcnow_iso

LATEST_SCHEMA_VERSION = 1

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    adapter_kind TEXT NOT NULL,
    display_name TEXT,
    external_ref TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    author TEXT,
    published_at TEXT,
    updated_at TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    checksum TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, external_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_items_source_updated ON items(source_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_source_external ON items(source_id, external_id);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    start_offset INTEGER,
    end_offset INTEGER,
    checksum TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(item_id, ordinal),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_item_ordinal ON chunks(item_id, ordinal);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    item_id UNINDEXED,
    source_id UNINDEXED,
    chunk_text,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_id, item_id, source_id, chunk_text)
    SELECT NEW.id, NEW.id, NEW.item_id, i.source_id, NEW.text
    FROM items AS i
    WHERE i.id = NEW.item_id;
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    UPDATE chunks_fts
    SET chunk_id = NEW.id,
        item_id = NEW.item_id,
        source_id = (
            SELECT i.source_id
            FROM items AS i
            WHERE i.id = NEW.item_id
        ),
        chunk_text = NEW.text
    WHERE rowid = NEW.id;
END;

CREATE TABLE IF NOT EXISTS citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL,
    citation_key TEXT NOT NULL,
    locator TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chunk_id, citation_key, locator),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_citations_chunk ON citations(chunk_id);
CREATE INDEX IF NOT EXISTS idx_citations_key ON citations(citation_key);

CREATE TABLE IF NOT EXISTS sync_state (
    source_id INTEGER PRIMARY KEY,
    checkpoint_cursor TEXT,
    checkpoint_time TEXT,
    full_sync_required INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chunk_id, entity_type, entity_value),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entity_links_lookup
    ON entity_links(entity_type, entity_value);
"""


class PKMStore:
    """SQLite-backed persistence core for Personal Knowledge Manager."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        return self._require_connection()

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._conn = conn
        self._migrate()

    def close(self) -> None:
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    def schema_version(self) -> int:
        row = self._require_connection().execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def register_source(
        self,
        source_key: str,
        adapter_kind: str,
        *,
        display_name: str | None = None,
        external_ref: str | None = None,
        enabled: bool = True,
    ) -> int:
        now = utcnow_iso()
        conn = self._require_connection()
        conn.execute(
            """
            INSERT INTO sources(
                source_key, adapter_kind, display_name, external_ref, enabled, created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                adapter_kind = excluded.adapter_kind,
                display_name = COALESCE(excluded.display_name, sources.display_name),
                external_ref = COALESCE(excluded.external_ref, sources.external_ref),
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                source_key,
                adapter_kind,
                display_name,
                external_ref,
                int(enabled),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM sources WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        conn.commit()
        if row is None:
            raise RuntimeError(f"Failed to register source: {source_key}")
        return int(row["id"])

    def get_checkpoint(self, source_key: str) -> SyncCheckpoint | None:
        row = self._require_connection().execute(
            """
            SELECT
                s.id AS source_id,
                s.source_key AS source_key,
                ss.checkpoint_cursor AS checkpoint_cursor,
                ss.checkpoint_time AS checkpoint_time,
                ss.full_sync_required AS full_sync_required,
                ss.last_success_at AS last_success_at,
                ss.last_attempt_at AS last_attempt_at,
                ss.last_error AS last_error,
                ss.metadata_json AS metadata_json
            FROM sources AS s
            LEFT JOIN sync_state AS ss ON ss.source_id = s.id
            WHERE s.source_key = ?
            """,
            (source_key,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_checkpoint(row)

    def save_checkpoint(
        self,
        source_key: str,
        *,
        cursor: str | None,
        checkpoint_time: str | None,
        metadata: Mapping[str, Any] | None = None,
        full_sync_required: bool = False,
    ) -> SyncCheckpoint:
        source_id = self._require_source_id(source_key)
        now = utcnow_iso()
        conn = self._require_connection()
        conn.execute(
            """
            INSERT INTO sync_state(
                source_id,
                checkpoint_cursor,
                checkpoint_time,
                full_sync_required,
                last_success_at,
                last_attempt_at,
                last_error,
                metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                checkpoint_cursor = excluded.checkpoint_cursor,
                checkpoint_time = excluded.checkpoint_time,
                full_sync_required = excluded.full_sync_required,
                last_success_at = excluded.last_success_at,
                last_attempt_at = excluded.last_attempt_at,
                last_error = NULL,
                metadata_json = excluded.metadata_json
            """,
            (
                source_id,
                cursor,
                checkpoint_time,
                int(full_sync_required),
                now,
                now,
                _json_dumps(metadata),
            ),
        )
        conn.commit()
        checkpoint = self.get_checkpoint(source_key)
        if checkpoint is None:
            raise RuntimeError(f"Checkpoint unexpectedly missing for source: {source_key}")
        return checkpoint

    def record_sync_failure(
        self,
        source_key: str,
        error: str,
        *,
        full_sync_required: bool | None = None,
    ) -> SyncCheckpoint:
        source_id = self._require_source_id(source_key)
        current = self.get_checkpoint(source_key)
        required = (
            current.full_sync_required
            if (current is not None and full_sync_required is None)
            else bool(full_sync_required if full_sync_required is not None else True)
        )
        metadata = current.metadata if current is not None else {}
        conn = self._require_connection()
        conn.execute(
            """
            INSERT INTO sync_state(
                source_id,
                checkpoint_cursor,
                checkpoint_time,
                full_sync_required,
                last_success_at,
                last_attempt_at,
                last_error,
                metadata_json
            )
            VALUES(?, NULL, NULL, ?, NULL, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                full_sync_required = excluded.full_sync_required,
                last_attempt_at = excluded.last_attempt_at,
                last_error = excluded.last_error,
                metadata_json = excluded.metadata_json
            """,
            (
                source_id,
                int(required),
                utcnow_iso(),
                error,
                _json_dumps(metadata),
            ),
        )
        conn.commit()
        checkpoint = self.get_checkpoint(source_key)
        if checkpoint is None:
            raise RuntimeError(f"Checkpoint unexpectedly missing for source: {source_key}")
        return checkpoint

    def search_chunks(self, query: str, *, limit: int = 20) -> list[sqlite3.Row]:
        rows = self._require_connection().execute(
            """
            SELECT
                c.id AS chunk_id,
                c.item_id AS item_id,
                i.source_id AS source_id,
                s.source_key AS source_key,
                c.ordinal AS ordinal,
                c.text AS text,
                snippet(chunks_fts, 3, '[', ']', '…', 12) AS snippet
            FROM chunks_fts
            JOIN chunks AS c ON c.id = chunks_fts.rowid
            JOIN items AS i ON i.id = c.item_id
            JOIN sources AS s ON s.id = i.source_id
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts), c.id
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return list(rows)

    def _migrate(self) -> None:
        conn = self._require_connection()
        version = self.schema_version()
        if version > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than supported {LATEST_SCHEMA_VERSION}"
            )
        while version < LATEST_SCHEMA_VERSION:
            next_version = version + 1
            if next_version == 1:
                self._apply_migration_001(conn)
            else:
                raise RuntimeError(f"Missing migration implementation for version {next_version}")
            conn.execute(f"PRAGMA user_version = {next_version}")
            version = next_version
        conn.commit()

    def _apply_migration_001(self, conn: sqlite3.Connection) -> None:
        conn.executescript(MIGRATION_001)
        conn.execute(
            """
            INSERT OR REPLACE INTO chunks_fts(rowid, chunk_id, item_id, source_id, chunk_text)
            SELECT c.id, c.id, c.item_id, i.source_id, c.text
            FROM chunks AS c
            JOIN items AS i ON i.id = c.item_id
            """
        )

    def _require_source_id(self, source_key: str) -> int:
        row = self._require_connection().execute(
            "SELECT id FROM sources WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        if row is None:
            raise KeyError(source_key)
        return int(row["id"])

    def _require_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store is not open")
        return self._conn


def _row_to_checkpoint(row: sqlite3.Row) -> SyncCheckpoint:
    full_sync_required = (
        True if row["full_sync_required"] is None else bool(row["full_sync_required"])
    )
    metadata = _json_loads(row["metadata_json"])
    return SyncCheckpoint(
        source_key=str(row["source_key"]),
        source_id=int(row["source_id"]),
        cursor=row["checkpoint_cursor"],
        checkpoint_time=row["checkpoint_time"],
        full_sync_required=full_sync_required,
        last_success_at=row["last_success_at"],
        last_attempt_at=row["last_attempt_at"],
        last_error=row["last_error"],
        metadata=metadata,
    )


def _json_dumps(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return "{}"
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}
