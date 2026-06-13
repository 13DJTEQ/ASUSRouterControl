"""Notion adapter with fixture-friendly provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pkm.adapters.base import (
    AdapterChunk,
    AdapterCitation,
    AdapterEntityLink,
    AdapterItem,
    SyncBatch,
)
from pkm.checkpoints import SyncCheckpoint
from pkm.utils import chunk_text, first_line_summary, utcnow_iso


@runtime_checkable
class NotionRecordProvider(Protocol):
    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[Mapping[str, Any]], str | None, str]:
        """Fetch raw Notion records plus next cursor/checkpoint time."""


@dataclass(slots=True)
class FixtureNotionProvider:
    records: Sequence[Mapping[str, Any]]

    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[Mapping[str, Any]], str | None, str]:
        offset = _cursor_offset(cursor)
        selected = list(self.records[offset : offset + limit])
        next_cursor = str(offset + limit) if (offset + limit) < len(self.records) else None
        return selected, next_cursor, utcnow_iso()


class NotionAdapter:
    adapter_kind = "notion"

    def __init__(self, source_key: str, provider: NotionRecordProvider) -> None:
        self.source_key = source_key
        self._provider = provider

    async def pull(
        self,
        checkpoint: SyncCheckpoint | None,
        *,
        limit: int = 100,
    ) -> SyncBatch:
        cursor = checkpoint.cursor if checkpoint else None
        records, next_cursor, checkpoint_time = await self._provider.fetch(cursor, limit=limit)
        return SyncBatch(
            items=[self._normalize(record) for record in records],
            next_cursor=next_cursor,
            checkpoint_time=checkpoint_time,
            metadata={"record_count": len(records)},
        )

    def _normalize(self, record: Mapping[str, Any]) -> AdapterItem:
        page_id = str(record.get("id") or record.get("page_id") or "unknown")
        title = (
            _optional_text(record.get("title")) or _optional_text(record.get("name")) or page_id
        )
        body = _optional_text(record.get("content")) or _optional_text(
            record.get("plain_text")
        ) or ""
        summary = first_line_summary(body) or title
        updated_at = _optional_text(record.get("last_edited_time")) or _optional_text(
            record.get("updated_at")
        )
        published_at = _optional_text(record.get("created_time"))
        author = _optional_text(record.get("author")) or _optional_text(record.get("created_by"))
        url = _optional_text(record.get("url")) or f"notion://{page_id}"
        content = f"{title}\n\n{body}".strip()
        chunks = [
            AdapterChunk(
                external_item_id=page_id,
                ordinal=ordinal,
                text=text_chunk,
                citations=(
                    AdapterCitation(
                        key=url,
                        locator=f"page:{page_id}#chunk-{ordinal}",
                    ),
                ),
                entity_links=(AdapterEntityLink("notion_page", page_id, 1.0),),
            )
            for ordinal, text_chunk in enumerate(chunk_text(content))
        ]
        payload = {
            "url": url,
            "database_id": _optional_text(record.get("database_id")),
            "workspace": _optional_text(record.get("workspace")),
        }
        return AdapterItem(
            external_id=page_id,
            title=title,
            summary=summary,
            author=author,
            published_at=published_at,
            updated_at=updated_at,
            payload=payload,
            chunks=chunks,
        )


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        parsed = int(cursor)
    except ValueError:
        return 0
    return max(parsed, 0)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
