"""Command line interface for PKM sync and retrieval workflows."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence

import click

from pkm.adapters import (
    AppleMailAdapter,
    FilesystemLocalRepositoryProvider,
    FixtureAppleMailProvider,
    FixtureGitHubProvider,
    FixtureLocalRepositoryProvider,
    FixtureNotionProvider,
    GitHubAdapter,
    LocalRepositoryAdapter,
    NotionAdapter,
    PKMSourceAdapter,
)
from pkm.retrieval import CitationRef, RetrievalService, format_citation
from pkm.store import PKMStore
from pkm.syncer import SyncOrchestrator


@click.group(help="Personal Knowledge Manager CLI.")
def cli() -> None:
    """PKM command group."""


@cli.command("sync", help="Synchronize one source into local PKM storage.")
@click.option(
    "--db-path",
    default="pkm.db",
    show_default=True,
    type=click.Path(path_type=Path),
    help="SQLite database path.",
)
@click.option("--source-key", required=True, help="Source key to synchronize.")
@click.option(
    "--adapter",
    "adapter_kind",
    default="local",
    show_default=True,
    type=click.Choice(["local", "github", "notion", "apple_mail"]),
)
@click.option(
    "--repo-path",
    "repo_paths",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Repository root path(s) for local adapter mode.",
)
@click.option(
    "--fixture-file",
    type=click.Path(path_type=Path, exists=True),
    help="Fixture JSON file for deterministic adapter fetches.",
)
@click.option("--limit", default=100, show_default=True, type=int)
def sync_command(
    db_path: Path,
    source_key: str,
    adapter_kind: str,
    repo_paths: Sequence[Path],
    fixture_file: Path | None,
    limit: int,
) -> None:
    adapter = _build_adapter(
        source_key=source_key,
        adapter_kind=adapter_kind,
        repo_paths=repo_paths,
        fixture_file=fixture_file,
    )
    with _open_store(db_path) as store:
        orchestrator = SyncOrchestrator(store, [adapter])
        result = asyncio.run(orchestrator.sync_source(source_key, limit=limit))
    click.echo(
        "synced "
        f"source={result.source_key} fetched={result.fetched_count} "
        f"inserted={result.stats.inserted_items} updated={result.stats.updated_items} "
        f"unchanged={result.stats.unchanged_items} next_cursor={result.next_cursor or '-'}"
    )


@cli.command("search", help="Search PKM chunks with citation-backed results.")
@click.argument("query")
@click.option(
    "--db-path",
    default="pkm.db",
    show_default=True,
    type=click.Path(path_type=Path),
    help="SQLite database path.",
)
@click.option("--source-key", help="Limit search to one source key.")
@click.option("--limit", default=10, show_default=True, type=int)
def search_command(query: str, db_path: Path, source_key: str | None, limit: int) -> None:
    with _open_store(db_path) as store:
        service = RetrievalService(store)
        hits = service.search(query, limit=limit, source_key=source_key)
    if not hits:
        click.echo("No citation-backed matches found.")
        return
    for index, hit in enumerate(hits, start=1):
        click.echo(f"{index}. [{hit.source_key}] {hit.title}")
        click.echo(f"   {_single_line(hit.snippet)}")
        click.echo(f"   citations: {_format_citations(hit.citations)}")


@cli.command("brief", help="Generate a citation-backed topic brief.")
@click.argument("topic")
@click.option(
    "--db-path",
    default="pkm.db",
    show_default=True,
    type=click.Path(path_type=Path),
    help="SQLite database path.",
)
@click.option("--source-key", help="Limit brief generation to one source key.")
@click.option("--window", default="7d", show_default=True, help="Time window (e.g. 24h, 7d, 2w).")
@click.option("--limit", default=5, show_default=True, type=int)
def brief_command(
    topic: str,
    db_path: Path,
    source_key: str | None,
    window: str,
    limit: int,
) -> None:
    with _open_store(db_path) as store:
        service = RetrievalService(store)
        brief = service.brief(topic, window=window, limit=limit, source_key=source_key)
    click.echo(f"Brief: {topic} ({window})")
    if not brief.points:
        click.echo("- No citation-backed points available.")
        return
    for point in brief.points:
        click.echo(f"- {point}")
    click.echo("Citations:")
    for citation in brief.citations:
        click.echo(f"- {format_citation(citation)}")


@cli.command("timeline", help="Show a citation-backed timeline of recent PKM events.")
@click.option(
    "--db-path",
    default="pkm.db",
    show_default=True,
    type=click.Path(path_type=Path),
    help="SQLite database path.",
)
@click.option("--source-key", help="Limit timeline to one source key.")
@click.option("--limit", default=50, show_default=True, type=int)
def timeline_command(db_path: Path, source_key: str | None, limit: int) -> None:
    with _open_store(db_path) as store:
        service = RetrievalService(store)
        entries = service.timeline(source_key=source_key, limit=limit)
    if not entries:
        click.echo("No citation-backed timeline entries found.")
        return
    for entry in entries:
        event_time = entry.event_time or "unknown-time"
        click.echo(f"- {event_time} [{entry.source_key}] {entry.title}")
        click.echo(f"  {_single_line(entry.snippet)}")
        click.echo(f"  citations: {_format_citations(entry.citations)}")


def _build_adapter(
    *,
    source_key: str,
    adapter_kind: str,
    repo_paths: Sequence[Path],
    fixture_file: Path | None,
) -> PKMSourceAdapter:
    fixture_records = _load_fixture_records(fixture_file) if fixture_file else None
    if adapter_kind == "local":
        if fixture_records is not None:
            provider = FixtureLocalRepositoryProvider(fixture_records)
            return LocalRepositoryAdapter(source_key, provider)
        roots = tuple(repo_paths) if repo_paths else (Path.cwd(),)
        return LocalRepositoryAdapter(source_key, FilesystemLocalRepositoryProvider(roots))
    if fixture_records is None:
        raise click.ClickException(
            f"{adapter_kind} adapter requires --fixture-file in this runtime mode."
        )
    if adapter_kind == "github":
        return GitHubAdapter(source_key, FixtureGitHubProvider(fixture_records))
    if adapter_kind == "notion":
        return NotionAdapter(source_key, FixtureNotionProvider(fixture_records))
    if adapter_kind == "apple_mail":
        return AppleMailAdapter(source_key, FixtureAppleMailProvider(fixture_records))
    raise click.ClickException(f"Unsupported adapter kind: {adapter_kind}")


def _load_fixture_records(path: Path) -> list[dict[str, object]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Failed to load fixture JSON: {path}") from exc
    if not isinstance(parsed, list):
        raise click.ClickException("Fixture JSON must be an array of objects.")
    records: list[dict[str, object]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise click.ClickException(f"Fixture entry at index {index} is not an object.")
        records.append(item)
    return records


@contextmanager
def _open_store(db_path: Path):
    store = PKMStore(db_path)
    store.open()
    try:
        yield store
    finally:
        store.close()


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _format_citations(citations: Sequence[CitationRef]) -> str:
    if not citations:
        return "-"
    return ", ".join(format_citation(citation) for citation in citations)
