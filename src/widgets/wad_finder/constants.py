"""Module-level constants for the WAD finder (Qt-free)."""

from __future__ import annotations

from src.config import DEFAULT_BROWSER_SOURCES as CONFIG_DEFAULT_SOURCES

UA = "BFG.py wad browser/2.0 (+https://github.com)"
DEFAULT_EXTENSIONS = (".wad", ".pk3", ".ipk3", ".pk7", ".pke", ".zip")
INDEX_TTL_SECONDS = 60 * 60 * 6
DEFAULT_SOURCE_LIMIT = 750
MAX_CACHED_INDEX_ENTRIES = 5000
MAX_INDEX_FETCH_BYTES = 64 * 1024 * 1024           # compressed index cap
MAX_INDEX_DECOMPRESSED_BYTES = 256 * 1024 * 1024   # gzip-bomb guard
QUERY_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}
SOURCE_STATUS_UNKNOWN = "unknown"
SOURCE_STATUS_CHECKING = "checking"
SOURCE_STATUS_CACHED = "cached"
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_UNREACHABLE = "unreachable"
SOURCE_STATUS_ERROR = "error"
SOURCE_STATUS_DISABLED = "disabled"
SOURCE_STATUSES = frozenset(
    {
        SOURCE_STATUS_UNKNOWN,
        SOURCE_STATUS_CHECKING,
        SOURCE_STATUS_CACHED,
        SOURCE_STATUS_OK,
        SOURCE_STATUS_UNREACHABLE,
        SOURCE_STATUS_ERROR,
        SOURCE_STATUS_DISABLED,
    }
)
MIRRORS_DISCOVERY_API_URLS = [
    "https://www.doomworld.com/idgames/api/api.php?out=json&action=mirrors",
]
HTML_CRAWL_MAX_DEPTH = 3
HTML_CRAWL_MAX_DIRS = 120
HTML_CRAWL_MAX_FILES = 2200
MIRRORS_DISCOVERY_URL = "https://www.gamers.org/ftp/archives.html"
MIRRORS_DISCOVERY_URLS = [
    MIRRORS_DISCOVERY_URL,
    "https://www.doomworld.com/idgames/",
    "https://www.doomworld.com/idgames/index.php",
    "https://doomwiki.org/wiki/Idgames",
]
MIRRORS_DISCOVERY_MAX_DEPTH = 2
MIRRORS_DISCOVERY_MAX_PAGES = 280
MIRRORS_DISCOVERY_TIMEOUT = 25
SOURCE_PING_TIMEOUT = 8
IDGAMES_API_TIMEOUT = 25
IDGAMES_TEXTFILE_TIMEOUT = 14
IDGAMES_TEXTFILE_MAX_FETCHES = 90
IDGAMES_TEXTFILE_MIN_TOKENS = 2
PARSER_ALIASES = {
    "api": "idgames_api",
    "idgames_api": "idgames_api",
    "idgamesapi": "idgames_api",
    "id_api": "idgames_api",
    "id-api": "idgames_api",
    "idgames": "idgames_api",
    "crawl": "html",
    "json": "json",
    "jsn": "json",
    "js": "json",
    "text": "text",
    "txt": "text",
    "rss": "rss",
    "atom": "rss",
    "xml": "rss",
    "crawler": "html",
    "webcrawl": "html",
    "web_crawl": "html",
    "html": "html",
    "fullsort": "fullsort",
    "auto": "auto",
}
KNOWN_PARSERS = frozenset({"fullsort", "html", "auto", "idgames_api", "json", "rss", "text"})

DEFAULT_SOURCE_PARSER = "fullsort"

DEFAULT_SOURCES = [
    source.to_dict() if hasattr(source, "to_dict") else dict(source)
    for source in CONFIG_DEFAULT_SOURCES
]
