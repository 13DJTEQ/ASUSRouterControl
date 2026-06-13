"""Apple Mail adapter with fixture-friendly provider abstraction."""

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
class AppleMailRecordProvider(Protocol):
    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[Mapping[str, Any]], str | None, str]:
        """Fetch raw Apple Mail records plus next cursor/checkpoint time."""


@dataclass(slots=True)
class FixtureAppleMailProvider:
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


class AppleMailAdapter:
    adapter_kind = "apple_mail"

    def __init__(self, source_key: str, provider: AppleMailRecordProvider) -> None:
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
        message_id = str(record.get("message_id") or record.get("id") or "unknown")
        subject = _optional_text(record.get("subject")) or "(no subject)"
        body = _optional_text(record.get("body")) or _optional_text(record.get("snippet")) or ""
        sender = _optional_text(record.get("sender")) or _optional_text(record.get("from"))
        received_at = _optional_text(record.get("received_at")) or _optional_text(
            record.get("date")
        )
        mailbox = _optional_text(record.get("mailbox")) or "inbox"
        url = _optional_text(record.get("url")) or f"message://{message_id}"
        content = f"{subject}\n\n{body}".strip()
        chunks = [
            AdapterChunk(
                external_item_id=message_id,
                ordinal=ordinal,
                text=text_chunk,
                citations=(
                    AdapterCitation(
                        key=url,
                        locator=f"message:{message_id}#chunk-{ordinal}",
                        metadata={"mailbox": mailbox},
                    ),
                ),
                entity_links=(
                    AdapterEntityLink("sender", sender or "unknown", 1.0),
                    AdapterEntityLink("mailbox", mailbox, 1.0),
                ),
            )
            for ordinal, text_chunk in enumerate(chunk_text(content))
        ]
        payload = {
            "mailbox": mailbox,
            "thread_id": _optional_text(record.get("thread_id")),
            "to": _optional_text(record.get("to")),
            "cc": _optional_text(record.get("cc")),
            "url": url,
        }
        return AdapterItem(
            external_id=message_id,
            title=subject,
            summary=first_line_summary(body) or subject,
            author=sender,
            published_at=received_at,
            updated_at=received_at,
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
