"""Local repository adapter for PKM synchronization."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pkm.adapters.base import (
    AdapterChunk,
    AdapterCitation,
    AdapterEntityLink,
    AdapterItem,
    SyncBatch,
)
from pkm.checkpoints import SyncCheckpoint
from pkm.utils import chunk_text, first_line_summary, stable_hash, utcnow_iso

_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


@dataclass(frozen=True, slots=True)
class LocalFileRecord:
    repository: str
    repo_root: str
    relative_path: str
    absolute_path: str
    content: str
    updated_at: str
    git_commit: str | None = None
    git_author: str | None = None


@runtime_checkable
class LocalRepositoryProvider(Protocol):
    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[LocalFileRecord], str | None, str]:
        """Return local file records, next cursor, and checkpoint time."""


class FilesystemLocalRepositoryProvider:
    """Discovers text files from local repositories."""

    def __init__(
        self,
        roots: Sequence[Path],
        *,
        suffixes: Sequence[str] | None = None,
        max_file_bytes: int = 512 * 1024,
    ) -> None:
        self._roots = tuple(Path(root).resolve() for root in roots)
        self._suffixes = {suffix.lower() for suffix in (suffixes or tuple(_TEXT_SUFFIXES))}
        self._max_file_bytes = max_file_bytes

    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[LocalFileRecord], str | None, str]:
        offset = _cursor_offset(cursor)
        records = self._collect_records()
        selected = records[offset : offset + limit]
        next_cursor = str(offset + limit) if (offset + limit) < len(records) else None
        return selected, next_cursor, utcnow_iso()

    def _collect_records(self) -> list[LocalFileRecord]:
        records: list[LocalFileRecord] = []
        for root in self._roots:
            if not root.exists():
                continue
            git_commit, git_author = _read_git_head(root)
            repository = root.name
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if _is_skipped(path):
                    continue
                if path.suffix.lower() not in self._suffixes:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size <= 0 or stat.st_size > self._max_file_bytes:
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if not content.strip():
                    continue
                records.append(
                    LocalFileRecord(
                        repository=repository,
                        repo_root=str(root),
                        relative_path=str(path.relative_to(root)),
                        absolute_path=str(path),
                        content=content,
                        updated_at=utcnow_iso(),
                        git_commit=git_commit,
                        git_author=git_author,
                    )
                )
        records.sort(key=lambda record: (record.repository, record.relative_path))
        return records


class FixtureLocalRepositoryProvider:
    """Deterministic provider for tests and fixture-driven sync."""

    def __init__(self, records: Sequence[LocalFileRecord | Mapping[str, Any]]) -> None:
        normalized: list[LocalFileRecord] = []
        for record in records:
            if isinstance(record, LocalFileRecord):
                normalized.append(record)
                continue
            normalized.append(
                LocalFileRecord(
                    repository=str(record.get("repository", "fixture-repo")),
                    repo_root=str(record.get("repo_root", "/fixture/repo")),
                    relative_path=str(record.get("relative_path", "document.txt")),
                    absolute_path=str(record.get("absolute_path", "/fixture/repo/document.txt")),
                    content=str(record.get("content", "")),
                    updated_at=str(record.get("updated_at", utcnow_iso())),
                    git_commit=_optional_text(record.get("git_commit")),
                    git_author=_optional_text(record.get("git_author")),
                )
            )
        self._records = sorted(
            normalized,
            key=lambda record: (record.repository, record.relative_path),
        )

    async def fetch(
        self,
        cursor: str | None,
        *,
        limit: int = 100,
    ) -> tuple[Sequence[LocalFileRecord], str | None, str]:
        offset = _cursor_offset(cursor)
        selected = self._records[offset : offset + limit]
        next_cursor = str(offset + limit) if (offset + limit) < len(self._records) else None
        return selected, next_cursor, utcnow_iso()


class LocalRepositoryAdapter:
    """Normalizes repository files into PKM items/chunks."""

    adapter_kind = "local_repository"

    def __init__(
        self,
        source_key: str,
        provider: LocalRepositoryProvider,
    ) -> None:
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
        items = [self._record_to_item(record) for record in records]
        metadata = {"record_count": len(records)}
        return SyncBatch(
            items=items,
            next_cursor=next_cursor,
            checkpoint_time=checkpoint_time,
            full_sync_required=False,
            metadata=metadata,
        )

    def _record_to_item(self, record: LocalFileRecord) -> AdapterItem:
        external_id = f"{record.repository}:{record.relative_path}"
        title = record.relative_path
        summary = first_line_summary(record.content)
        text_chunks = chunk_text(record.content, chunk_size=1000, overlap=160)
        citations = [
            AdapterCitation(
                key=f"file://{record.repository}/{record.relative_path}",
                locator=f"path:{record.relative_path}",
                metadata={"absolute_path": record.absolute_path},
            )
        ]
        item_chunks = [
            AdapterChunk(
                external_item_id=external_id,
                ordinal=ordinal,
                text=text_chunk,
                citations=citations,
                entity_links=(
                    AdapterEntityLink("repository", record.repository, 1.0),
                    AdapterEntityLink(
                        "file_extension",
                        Path(record.relative_path).suffix or "<none>",
                        0.9,
                    ),
                ),
                metadata={"relative_path": record.relative_path},
            )
            for ordinal, text_chunk in enumerate(text_chunks)
        ]
        payload = {
            "repository": record.repository,
            "repo_root": record.repo_root,
            "relative_path": record.relative_path,
            "absolute_path": record.absolute_path,
            "git_commit": record.git_commit,
            "git_author": record.git_author,
            "content_hash": stable_hash([record.content]),
        }
        return AdapterItem(
            external_id=external_id,
            title=title,
            summary=summary,
            author=record.git_author,
            updated_at=record.updated_at,
            payload=payload,
            chunks=item_chunks,
        )


def _is_skipped(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _read_git_head(repo_root: Path) -> tuple[str | None, str | None]:
    command = [
        "git",
        "-C",
        str(repo_root),
        "--no-pager",
        "log",
        "-1",
        "--format=%H%x1f%an",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    output = completed.stdout.strip()
    if not output:
        return None, None
    commit, _, author = output.partition("\x1f")
    return _optional_text(commit), _optional_text(author)


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        parsed = int(cursor)
    except ValueError:
        return 0
    return max(0, parsed)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
