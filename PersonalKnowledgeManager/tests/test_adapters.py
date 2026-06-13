from __future__ import annotations

import pytest
from pkm.adapters import (
    AppleMailAdapter,
    FixtureAppleMailProvider,
    FixtureGitHubProvider,
    FixtureLocalRepositoryProvider,
    FixtureNotionProvider,
    GitHubAdapter,
    LocalRepositoryAdapter,
    NotionAdapter,
)


@pytest.mark.asyncio
async def test_local_adapter_fixture_normalizes_chunks_and_citations() -> None:
    provider = FixtureLocalRepositoryProvider(
        [
            {
                "repository": "repo-a",
                "repo_root": "/tmp/repo-a",
                "relative_path": "notes.md",
                "absolute_path": "/tmp/repo-a/notes.md",
                "content": ("router outage retrospective\n" * 60).strip(),
                "updated_at": "2026-06-12T00:00:00Z",
                "git_author": "alice",
            }
        ]
    )
    adapter = LocalRepositoryAdapter("local-repo", provider)
    batch = await adapter.pull(None, limit=10)
    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.external_id == "repo-a:notes.md"
    assert item.chunks
    assert item.chunks[0].citations
    assert item.chunks[0].entity_links


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "expected_prefix"),
    [
        (
            GitHubAdapter(
                "gh-fixture",
                FixtureGitHubProvider(
                    [
                        {
                            "repository": "org/repo",
                            "id": 123,
                            "kind": "issue",
                            "title": "Packet loss spike",
                            "body": "Observed around 14:00 UTC.",
                            "updated_at": "2026-06-12T01:00:00Z",
                            "url": "https://github.com/org/repo/issues/123",
                        }
                    ]
                ),
            ),
            "org/repo:issue:123",
        ),
        (
            NotionAdapter(
                "notion-fixture",
                FixtureNotionProvider(
                    [
                        {
                            "id": "page-1",
                            "title": "Incident Log",
                            "content": "Follow-up tasks and owners.",
                            "last_edited_time": "2026-06-12T02:00:00Z",
                            "url": "https://notion.so/page-1",
                        }
                    ]
                ),
            ),
            "page-1",
        ),
        (
            AppleMailAdapter(
                "mail-fixture",
                FixtureAppleMailProvider(
                    [
                        {
                            "message_id": "msg-1",
                            "subject": "Router incident recap",
                            "body": "Please capture timeline + actions.",
                            "received_at": "2026-06-12T03:00:00Z",
                            "url": "message://msg-1",
                        }
                    ]
                ),
            ),
            "msg-1",
        ),
    ],
)
async def test_fixture_adapters_normalize_core_fields(adapter, expected_prefix: str) -> None:
    batch = await adapter.pull(None, limit=5)
    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.external_id.startswith(expected_prefix)
    assert item.chunks
    assert item.chunks[0].citations
