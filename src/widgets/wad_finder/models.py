"""Data models for the WAD finder (Qt-free)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SourceStatus(str, Enum):
    """Health status of a search source. String values are persisted in config."""

    UNKNOWN = "unknown"
    CHECKING = "checking"
    CACHED = "cached"
    OK = "ok"
    UNREACHABLE = "unreachable"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(slots=True)
class WadBrowserResult:
    title: str
    description: str
    metadata_text: str
    source_id: str
    source_name: str
    size_bytes: int
    remote_path: str
    download_url: str
    browser_url: str


@dataclass(slots=True)
class IndexCacheEntry:
    source_id: str
    source_name: str
    fetched_at: float
    entries: list[WadBrowserResult]


@dataclass(slots=True)
class Source:
    """A search source. ``to_dict``/``from_dict`` round-trip the persisted
    config shape (id/name/base/index/browser/parser/enabled/status/
    status_message/status_checked_at) byte-compatibly."""

    id: str
    name: str
    base: str
    index: str
    browser: str
    parser: str
    enabled: bool
    status: str
    status_message: str
    status_checked_at: float

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Source":
        # Local import: sources.py does not depend on this module.
        from .sources import coerce_source

        coerced = coerce_source(raw)
        return cls(**coerced)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base": self.base,
            "index": self.index,
            "browser": self.browser,
            "parser": self.parser,
            "enabled": self.enabled,
            "status": self.status,
            "status_message": self.status_message,
            "status_checked_at": self.status_checked_at,
        }
