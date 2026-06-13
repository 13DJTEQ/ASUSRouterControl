"""Adapter contracts for PKM source integrations."""

from pkm.adapters.apple_mail import AppleMailAdapter, FixtureAppleMailProvider
from pkm.adapters.base import (
    AdapterChunk,
    AdapterCitation,
    AdapterEntityLink,
    AdapterItem,
    PKMSourceAdapter,
    SyncBatch,
)
from pkm.adapters.github import FixtureGitHubProvider, GitHubAdapter
from pkm.adapters.local_repo import (
    FilesystemLocalRepositoryProvider,
    FixtureLocalRepositoryProvider,
    LocalFileRecord,
    LocalRepositoryAdapter,
    LocalRepositoryProvider,
)
from pkm.adapters.notion import FixtureNotionProvider, NotionAdapter

__all__ = [
    "AdapterChunk",
    "AdapterCitation",
    "AdapterEntityLink",
    "AdapterItem",
    "AppleMailAdapter",
    "FilesystemLocalRepositoryProvider",
    "FixtureAppleMailProvider",
    "FixtureGitHubProvider",
    "FixtureLocalRepositoryProvider",
    "FixtureNotionProvider",
    "GitHubAdapter",
    "LocalFileRecord",
    "LocalRepositoryAdapter",
    "LocalRepositoryProvider",
    "NotionAdapter",
    "PKMSourceAdapter",
    "SyncBatch",
]
