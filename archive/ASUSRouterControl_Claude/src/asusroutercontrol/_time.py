"""Timezone-aware datetime helpers.

Use :func:`utcnow` everywhere instead of ``datetime.utcnow()``.
``datetime.utcnow()`` is deprecated in Python 3.12 and returns a *naive*
datetime, which causes subtle comparison bugs against timezone-aware values
stored in SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware :class:`~datetime.datetime`."""
    return datetime.now(timezone.utc)
