"""Shared adapter contract for PKM source synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pkm.checkpoints import SyncCheckpoint


@dataclass(frozen=True, slots=True)
class AdapterCitation:
    """Citation attached to a normalized chunk."""

    key: str
    locator: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdapterEntityLink:
    """Entity association attached to a normalized chunk."""

    entity_type: str
    entity_value: str
    confidence: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdapterChunk:
    """Normalized chunk payload emitted by adapters."""

    external_item_id: str
    ordinal: int
    text: str
    citations: Sequence[AdapterCitation] = field(default_factory=tuple)
    entity_links: Sequence[AdapterEntityLink] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdapterItem:
    """Normalized item payload emitted by adapters."""

    external_id: str
    title: str | None = None
    summary: str | None = None
    author: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    chunks: Sequence[AdapterChunk] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SyncBatch:
    """Result returned by an adapter sync call."""

    items: Sequence[AdapterItem]
    next_cursor: str | None = None
    checkpoint_time: str | None = None
    full_sync_required: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PKMSourceAdapter(Protocol):
    """Minimal source adapter protocol for Phase A."""

    source_key: str
    adapter_kind: str

    async def pull(
        self,
        checkpoint: SyncCheckpoint | None,
        *,
        limit: int = 100,
    ) -> SyncBatch:
        """Fetch a batch of normalized source items."""
