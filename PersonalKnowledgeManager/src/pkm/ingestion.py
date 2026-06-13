"""Ingestion pipeline for writing normalized adapter payloads into PKM schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pkm.adapters.base import AdapterChunk, AdapterCitation, AdapterEntityLink, AdapterItem
from pkm.store import PKMStore
from pkm.utils import canonical_json, chunk_text, stable_hash, utcnow_iso


@dataclass(slots=True)
class IngestionStats:
    fetched_items: int = 0
    inserted_items: int = 0
    updated_items: int = 0
    unchanged_items: int = 0
    chunks_written: int = 0
    citations_written: int = 0
    entity_links_written: int = 0

    @property
    def ingested_items(self) -> int:
        return self.inserted_items + self.updated_items


class IngestionPipeline:
    """Persists adapter-normalized payloads with idempotent upserts."""

    def __init__(self, store: PKMStore) -> None:
        self._store = store

    def ingest_batch(
        self,
        *,
        source_key: str,
        adapter_kind: str,
        items: Sequence[AdapterItem],
    ) -> IngestionStats:
        source_id = self._store.register_source(source_key, adapter_kind)
        conn = self._store.connection
        stats = IngestionStats(fetched_items=len(items))
        conn.execute("BEGIN")
        try:
            for item in items:
                prepared_chunks = _normalize_chunks(item)
                item_id, state = self._upsert_item(conn, source_id, item, prepared_chunks)
                if state == "inserted":
                    stats.inserted_items += 1
                elif state == "updated":
                    stats.updated_items += 1
                else:
                    stats.unchanged_items += 1
                chunk_stats = self._upsert_chunks(
                    conn,
                    source_key=source_key,
                    item=item,
                    item_id=item_id,
                    chunks=prepared_chunks,
                )
                stats.chunks_written += chunk_stats.chunks_written
                stats.citations_written += chunk_stats.citations_written
                stats.entity_links_written += chunk_stats.entity_links_written
            conn.execute(
                "UPDATE sources SET updated_at = ? WHERE id = ?",
                (utcnow_iso(), source_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return stats

    def _upsert_item(
        self,
        conn,
        source_id: int,
        item: AdapterItem,
        chunks: Sequence[AdapterChunk],
    ) -> tuple[int, str]:
        payload_json = canonical_json(item.payload)
        checksum = _item_checksum(item, payload_json, chunks)
        existing = conn.execute(
            """
            SELECT id, checksum
            FROM items
            WHERE source_id = ? AND external_id = ?
            """,
            (source_id, item.external_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO items(
                    source_id, external_id, title, summary, author, published_at, updated_at,
                    raw_payload_json, checksum, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    item.external_id,
                    item.title,
                    item.summary,
                    item.author,
                    item.published_at,
                    item.updated_at,
                    payload_json,
                    checksum,
                    utcnow_iso(),
                ),
            )
            created = conn.execute(
                """
                SELECT id
                FROM items
                WHERE source_id = ? AND external_id = ?
                """,
                (source_id, item.external_id),
            ).fetchone()
            if created is None:
                raise RuntimeError(f"failed to insert item: {item.external_id}")
            return int(created["id"]), "inserted"
        item_id = int(existing["id"])
        if str(existing["checksum"] or "") == checksum:
            return item_id, "unchanged"
        conn.execute(
            """
            UPDATE items
            SET title = ?,
                summary = ?,
                author = ?,
                published_at = ?,
                updated_at = ?,
                raw_payload_json = ?,
                checksum = ?
            WHERE id = ?
            """,
            (
                item.title,
                item.summary,
                item.author,
                item.published_at,
                item.updated_at,
                payload_json,
                checksum,
                item_id,
            ),
        )
        return item_id, "updated"

    def _upsert_chunks(
        self,
        conn,
        *,
        source_key: str,
        item: AdapterItem,
        item_id: int,
        chunks: Sequence[AdapterChunk],
    ) -> IngestionStats:
        stats = IngestionStats()
        existing_rows = conn.execute(
            """
            SELECT id, ordinal, checksum
            FROM chunks
            WHERE item_id = ?
            """,
            (item_id,),
        ).fetchall()
        existing = {int(row["ordinal"]): row for row in existing_rows}
        kept_ordinals: list[int] = []
        for chunk in chunks:
            text = chunk.text.strip()
            if not text:
                continue
            checksum = _chunk_checksum(chunk)
            row = existing.get(chunk.ordinal)
            unchanged = row is not None and str(row["checksum"] or "") == checksum
            if row is None:
                conn.execute(
                    """
                    INSERT INTO chunks(
                        item_id, ordinal, text, token_count, start_offset, end_offset, checksum,
                        metadata_json, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        chunk.ordinal,
                        text,
                        _token_count(text),
                        None,
                        None,
                        checksum,
                        canonical_json(chunk.metadata),
                        utcnow_iso(),
                        utcnow_iso(),
                    ),
                )
                chunk_id = int(
                    conn.execute(
                        """
                        SELECT id
                        FROM chunks
                        WHERE item_id = ? AND ordinal = ?
                        """,
                        (item_id, chunk.ordinal),
                    ).fetchone()["id"]
                )
                stats.chunks_written += 1
            elif unchanged:
                chunk_id = int(row["id"])
            else:
                conn.execute(
                    """
                    UPDATE chunks
                    SET text = ?,
                        token_count = ?,
                        start_offset = ?,
                        end_offset = ?,
                        checksum = ?,
                        metadata_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        text,
                        _token_count(text),
                        None,
                        None,
                        checksum,
                        canonical_json(chunk.metadata),
                        utcnow_iso(),
                        int(row["id"]),
                    ),
                )
                chunk_id = int(row["id"])
                stats.chunks_written += 1
            kept_ordinals.append(chunk.ordinal)
            if unchanged:
                continue
            citations = _normalize_citations(
                chunk.citations,
                default_key=f"{source_key}:{item.external_id}",
                default_locator=f"chunk:{chunk.ordinal}",
            )
            entity_links = _normalize_entity_links(chunk.entity_links)
            stats.citations_written += _replace_chunk_citations(conn, chunk_id, citations)
            stats.entity_links_written += _replace_chunk_entity_links(conn, chunk_id, entity_links)
        _delete_stale_chunks(conn, item_id=item_id, kept_ordinals=kept_ordinals)
        return stats


def _normalize_chunks(item: AdapterItem) -> list[AdapterChunk]:
    if item.chunks:
        unique: dict[int, AdapterChunk] = {}
        for chunk in sorted(item.chunks, key=lambda value: value.ordinal):
            unique[chunk.ordinal] = chunk
        return list(unique.values())
    fallback_parts = [part for part in (item.title, item.summary) if part]
    if not fallback_parts and item.payload:
        fallback_parts.append(canonical_json(item.payload))
    fallback_text = "\n\n".join(fallback_parts).strip()
    return [
        AdapterChunk(external_item_id=item.external_id, ordinal=index, text=text_chunk)
        for index, text_chunk in enumerate(chunk_text(fallback_text))
    ]


def _item_checksum(item: AdapterItem, payload_json: str, chunks: Sequence[AdapterChunk]) -> str:
    chunk_hashes = [_chunk_checksum(chunk) for chunk in chunks]
    return stable_hash(
        [
            item.external_id,
            item.title or "",
            item.summary or "",
            item.author or "",
            item.published_at or "",
            item.updated_at or "",
            payload_json,
            canonical_json(chunk_hashes),
        ]
    )


def _chunk_checksum(chunk: AdapterChunk) -> str:
    citation_signature = [
        {"key": citation.key, "locator": citation.locator, "metadata": dict(citation.metadata)}
        for citation in _normalize_citations(chunk.citations, default_key="", default_locator="")
    ]
    entity_signature = [
        {
            "entity_type": entity.entity_type,
            "entity_value": entity.entity_value,
            "confidence": entity.confidence,
            "metadata": dict(entity.metadata),
        }
        for entity in _normalize_entity_links(chunk.entity_links)
    ]
    return stable_hash(
        [
            str(chunk.ordinal),
            chunk.text.strip(),
            canonical_json(chunk.metadata),
            canonical_json(citation_signature),
            canonical_json(entity_signature),
        ]
    )


def _normalize_citations(
    citations: Sequence[AdapterCitation],
    *,
    default_key: str,
    default_locator: str,
) -> list[AdapterCitation]:
    if not citations:
        if not default_key:
            return []
        return [AdapterCitation(key=default_key, locator=default_locator, metadata={})]
    deduped: dict[tuple[str, str], AdapterCitation] = {}
    for citation in citations:
        key = citation.key.strip()
        if not key:
            continue
        locator = citation.locator.strip() if citation.locator else default_locator
        deduped[(key, locator)] = AdapterCitation(
            key=key,
            locator=locator,
            metadata=citation.metadata,
        )
    if not deduped and default_key:
        return [AdapterCitation(key=default_key, locator=default_locator, metadata={})]
    return list(deduped.values())


def _normalize_entity_links(entities: Sequence[AdapterEntityLink]) -> list[AdapterEntityLink]:
    deduped: dict[tuple[str, str], AdapterEntityLink] = {}
    for entity in entities:
        entity_type = entity.entity_type.strip()
        entity_value = entity.entity_value.strip()
        if not entity_type or not entity_value:
            continue
        deduped[(entity_type, entity_value)] = AdapterEntityLink(
            entity_type=entity_type,
            entity_value=entity_value,
            confidence=max(0.0, min(1.0, float(entity.confidence))),
            metadata=entity.metadata,
        )
    return list(deduped.values())


def _replace_chunk_citations(conn, chunk_id: int, citations: Sequence[AdapterCitation]) -> int:
    conn.execute("DELETE FROM citations WHERE chunk_id = ?", (chunk_id,))
    inserted = 0
    for citation in citations:
        conn.execute(
            """
            INSERT INTO citations(chunk_id, citation_key, locator, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                citation.key,
                citation.locator,
                canonical_json(citation.metadata),
                utcnow_iso(),
            ),
        )
        inserted += 1
    return inserted


def _replace_chunk_entity_links(conn, chunk_id: int, entities: Sequence[AdapterEntityLink]) -> int:
    conn.execute("DELETE FROM entity_links WHERE chunk_id = ?", (chunk_id,))
    inserted = 0
    for entity in entities:
        conn.execute(
            """
            INSERT INTO entity_links(
                chunk_id, entity_type, entity_value, confidence, metadata_json, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                entity.entity_type,
                entity.entity_value,
                float(entity.confidence),
                canonical_json(entity.metadata),
                utcnow_iso(),
            ),
        )
        inserted += 1
    return inserted


def _delete_stale_chunks(conn, *, item_id: int, kept_ordinals: Sequence[int]) -> None:
    if kept_ordinals:
        placeholders = ", ".join("?" for _ in kept_ordinals)
        conn.execute(
            f"DELETE FROM chunks WHERE item_id = ? AND ordinal NOT IN ({placeholders})",
            (item_id, *kept_ordinals),
        )
        return
    conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))


def _token_count(text: str) -> int:
    return len(text.split())
