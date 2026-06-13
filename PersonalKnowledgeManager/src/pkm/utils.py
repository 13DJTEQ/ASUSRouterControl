"""Utility helpers used across PKM modules."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, (str, int, float, bool)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
    if isinstance(value, Sequence):
        return json.dumps(list(value), sort_keys=True, separators=(",", ":"))
    return json.dumps(str(value), sort_keys=True, separators=(",", ":"))


def stable_hash(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def chunk_text(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    index = 0
    length = len(cleaned)
    while index < length:
        end = min(index + chunk_size, length)
        if end < length:
            window_start = max(index + (chunk_size // 2), index)
            split_at = cleaned.rfind(" ", window_start, end)
            if split_at > index:
                end = split_at
        chunk = cleaned[index:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_index = max(index + 1, end - overlap)
        if next_index <= index:
            next_index = end
        index = next_index
    return chunks


def first_line_summary(text: str, *, max_len: int = 180) -> str:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            if len(candidate) <= max_len:
                return candidate
            return f"{candidate[: max_len - 1]}…"
    return ""
