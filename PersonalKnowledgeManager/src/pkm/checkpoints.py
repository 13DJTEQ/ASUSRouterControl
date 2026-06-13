"""Checkpoint primitives shared by store and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SyncCheckpoint:
    source_key: str
    source_id: int
    cursor: str | None
    checkpoint_time: str | None
    full_sync_required: bool
    last_success_at: str | None
    last_attempt_at: str | None
    last_error: str | None
    metadata: dict[str, Any]
