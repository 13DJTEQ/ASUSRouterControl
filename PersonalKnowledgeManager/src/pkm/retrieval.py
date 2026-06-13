"""Retrieval workflows for citation-backed PKM search, brief, and timeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from pkm.store import PKMStore
from pkm.utils import parse_iso8601


@dataclass(frozen=True, slots=True)
class CitationRef:
    key: str
    locator: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class SearchHit:
    score: float
    chunk_id: int
    source_key: str
    external_id: str
    title: str
    snippet: str
    updated_at: str | None
    citations: tuple[CitationRef, ...]


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    event_time: str | None
    source_key: str
    external_id: str
    title: str
    snippet: str
    citations: tuple[CitationRef, ...]


@dataclass(frozen=True, slots=True)
class BriefResult:
    topic: str
    window: str
    points: tuple[str, ...]
    citations: tuple[CitationRef, ...]


class RetrievalService:
    """Query helper that produces citation-backed retrieval views."""

    def __init__(self, store: PKMStore) -> None:
        self._store = store

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        source_key: str | None = None,
    ) -> list[SearchHit]:
        conn = self._store.connection
        sql = """
            SELECT
                c.id AS chunk_id,
                c.text AS chunk_text,
                i.external_id AS external_id,
                i.title AS title,
                COALESCE(i.updated_at, i.published_at, i.created_at) AS updated_at,
                s.source_key AS source_key,
                s.enabled AS source_enabled,
                bm25(chunks_fts) AS lexical_rank,
                snippet(chunks_fts, 3, '[', ']', '…', 16) AS snippet
            FROM chunks_fts
            JOIN chunks AS c ON c.id = chunks_fts.rowid
            JOIN items AS i ON i.id = c.item_id
            JOIN sources AS s ON s.id = i.source_id
            WHERE chunks_fts MATCH ?
        """
        params: list[object] = [query]
        if source_key:
            sql += " AND s.source_key = ?"
            params.append(source_key)
        sql += " ORDER BY bm25(chunks_fts), c.id LIMIT ?"
        params.append(max(limit, limit * 4))
        rows = conn.execute(sql, params).fetchall()
        ranked: list[SearchHit] = []
        for row in rows:
            citations = self._citations_for_chunk(
                chunk_id=int(row["chunk_id"]),
                source_key=str(row["source_key"]),
                external_id=str(row["external_id"]),
            )
            ranked.append(
                SearchHit(
                    score=_final_score(
                        lexical_rank=float(row["lexical_rank"] or 0.0),
                        updated_at=row["updated_at"],
                        source_enabled=bool(row["source_enabled"]),
                    ),
                    chunk_id=int(row["chunk_id"]),
                    source_key=str(row["source_key"]),
                    external_id=str(row["external_id"]),
                    title=str(row["title"] or row["external_id"]),
                    snippet=str(row["snippet"] or row["chunk_text"]),
                    updated_at=row["updated_at"],
                    citations=tuple(citations),
                )
            )
        ranked.sort(key=lambda hit: hit.score, reverse=True)
        return ranked[:limit]

    def timeline(
        self,
        *,
        source_key: str | None = None,
        limit: int = 50,
    ) -> list[TimelineEntry]:
        conn = self._store.connection
        sql = """
            SELECT
                c.id AS chunk_id,
                c.text AS chunk_text,
                i.external_id AS external_id,
                i.title AS title,
                COALESCE(i.updated_at, i.published_at, i.created_at) AS event_time,
                s.source_key AS source_key
            FROM chunks AS c
            JOIN items AS i ON i.id = c.item_id
            JOIN sources AS s ON s.id = i.source_id
            WHERE 1 = 1
        """
        params: list[object] = []
        if source_key:
            sql += " AND s.source_key = ?"
            params.append(source_key)
        sql += " ORDER BY event_time DESC, c.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        entries: list[TimelineEntry] = []
        for row in rows:
            citations = self._citations_for_chunk(
                chunk_id=int(row["chunk_id"]),
                source_key=str(row["source_key"]),
                external_id=str(row["external_id"]),
            )
            entries.append(
                TimelineEntry(
                    event_time=row["event_time"],
                    source_key=str(row["source_key"]),
                    external_id=str(row["external_id"]),
                    title=str(row["title"] or row["external_id"]),
                    snippet=_clean_snippet(str(row["chunk_text"])),
                    citations=tuple(citations),
                )
            )
        return entries

    def brief(
        self,
        topic: str,
        *,
        window: str = "7d",
        limit: int = 5,
        source_key: str | None = None,
    ) -> BriefResult:
        candidates = self.search(topic, limit=max(limit * 3, 12), source_key=source_key)
        cutoff = _window_cutoff(window)
        selected = _filter_hits_by_cutoff(candidates, cutoff) if cutoff else candidates
        selected = selected[:limit]
        points: list[str] = []
        citations: list[CitationRef] = []
        seen = set()
        for hit in selected:
            primary = hit.citations[0] if hit.citations else CitationRef("unknown", "", {})
            citation_label = format_citation(primary)
            points.append(f"{hit.title}: {_clean_snippet(hit.snippet)} [{citation_label}]")
            for citation in hit.citations:
                key = (citation.key, citation.locator)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(citation)
        return BriefResult(
            topic=topic,
            window=window,
            points=tuple(points),
            citations=tuple(citations),
        )

    def _citations_for_chunk(
        self,
        *,
        chunk_id: int,
        source_key: str,
        external_id: str,
    ) -> list[CitationRef]:
        rows = self._store.connection.execute(
            """
            SELECT citation_key, locator, metadata_json
            FROM citations
            WHERE chunk_id = ?
            ORDER BY id
            """,
            (chunk_id,),
        ).fetchall()
        citations: list[CitationRef] = []
        for row in rows:
            citations.append(
                CitationRef(
                    key=str(row["citation_key"]),
                    locator=str(row["locator"] or ""),
                    metadata=_safe_json_object(row["metadata_json"]),
                )
            )
        if citations:
            return citations
        return [
            CitationRef(
                key=f"{source_key}:{external_id}",
                locator=f"chunk:{chunk_id}",
                metadata={},
            )
        ]


def format_citation(citation: CitationRef) -> str:
    if citation.locator:
        return f"{citation.key}@{citation.locator}"
    return citation.key


def _filter_hits_by_cutoff(hits: Sequence[SearchHit], cutoff: datetime) -> list[SearchHit]:
    selected: list[SearchHit] = []
    for hit in hits:
        parsed = parse_iso8601(hit.updated_at)
        if parsed is None or parsed >= cutoff:
            selected.append(hit)
    return selected


def _window_cutoff(window: str) -> datetime | None:
    unit = window[-1:].lower()
    magnitude_raw = window[:-1]
    if not magnitude_raw:
        return None
    try:
        magnitude = int(magnitude_raw)
    except ValueError:
        return None
    if magnitude <= 0:
        return None
    if unit == "h":
        delta = timedelta(hours=magnitude)
    elif unit == "w":
        delta = timedelta(weeks=magnitude)
    else:
        delta = timedelta(days=magnitude)
    return datetime.now(timezone.utc) - delta


def _clean_snippet(value: str) -> str:
    cleaned = value.replace("\n", " ").replace("[", "").replace("]", "").strip()
    if len(cleaned) <= 220:
        return cleaned
    return f"{cleaned[:219]}…"


def _safe_json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _final_score(lexical_rank: float, updated_at: str | None, source_enabled: bool) -> float:
    lexical = 1.0 / (1.0 + abs(lexical_rank))
    recency = _recency_score(updated_at)
    source_boost = 0.05 if source_enabled else 0.0
    return (lexical * 0.80) + (recency * 0.15) + source_boost


def _recency_score(updated_at: str | None) -> float:
    parsed = parse_iso8601(updated_at)
    if parsed is None:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86_400.0, 0.0)
    return 1.0 / (1.0 + age_days)


def flatten_citations(entries: Iterable[Sequence[CitationRef]]) -> tuple[CitationRef, ...]:
    flattened: list[CitationRef] = []
    seen = set()
    for group in entries:
        for citation in group:
            key = (citation.key, citation.locator)
            if key in seen:
                continue
            seen.add(key)
            flattened.append(citation)
    return tuple(flattened)
