"""GitHub adapter with fixture-friendly provider abstraction."""

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
class GitHubRecordProvider(Protocol):
    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[Mapping[str, Any]], str | None, str]:
        """Fetch raw GitHub records plus next cursor/checkpoint time."""


@dataclass(slots=True)
class FixtureGitHubProvider:
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


class GitHubAdapter:
    adapter_kind = "github"

    def __init__(self, source_key: str, provider: GitHubRecordProvider) -> None:
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
        repository = str(record.get("repository") or record.get("repo") or "unknown/repo")
        item_id = str(
            record.get("id") or record.get("number") or record.get("node_id") or "unknown"
        )
        kind = str(record.get("kind") or record.get("type") or "issue")
        external_id = f"{repository}:{kind}:{item_id}"
        title = _optional_text(record.get("title")) or external_id
        body = _optional_text(record.get("body")) or ""
        summary = first_line_summary(body) or title
        updated_at = _optional_text(record.get("updated_at")) or _optional_text(
            record.get("last_updated")
        )
        published_at = _optional_text(record.get("created_at"))
        author = _optional_text(record.get("author")) or _optional_text(record.get("user"))
        url = _optional_text(record.get("url")) or _optional_text(record.get("html_url"))
        content = f"{title}\n\n{body}".strip()
        citation_key = url or f"github://{external_id}"
        chunks = [
            AdapterChunk(
                external_item_id=external_id,
                ordinal=ordinal,
                text=text_chunk,
                citations=(
                    AdapterCitation(
                        key=citation_key,
                        locator=f"{kind}:{item_id}#chunk-{ordinal}",
                        metadata={"repository": repository},
                    ),
                ),
                entity_links=(
                    AdapterEntityLink("repository", repository, 1.0),
                    AdapterEntityLink("github_kind", kind, 0.9),
                ),
            )
            for ordinal, text_chunk in enumerate(chunk_text(content))
        ]
        payload = {
            "repository": repository,
            "kind": kind,
            "url": url,
            "state": _optional_text(record.get("state")),
            "labels": record.get("labels") or [],
        }
        return AdapterItem(
            external_id=external_id,
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
