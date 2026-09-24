"""Pure text/query helpers for the WAD finder (Qt-free)."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any
from urllib.parse import unquote, urlparse

from .constants import DEFAULT_EXTENSIONS, QUERY_STOP_WORDS
from .models import WadBrowserResult


def looks_like_mod_file(path: str) -> bool:
    path = path.lower()
    return path.endswith(DEFAULT_EXTENSIONS)


def normalize_remote_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    if not path:
        return ""
    with suppress(Exception):
        path = unquote(path)
    return path.lstrip("/")


def result_identity(result: WadBrowserResult) -> str:
    remote_path = normalize_remote_path(result.remote_path).lower()
    if remote_path:
        return f"remote:{remote_path}"
    remote_url = normalize_remote_path(urlparse(result.download_url).path).lower()
    if remote_url:
        return f"url:{remote_url}"
    return f"name:{result.title.lower()}"


def normalize_metadata_text(*parts: Any) -> str:
    tokens: list[str] = []
    seen = set()
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            values = part
        else:
            values = [part]
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "").strip())
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            tokens.append(text)
    return " | ".join(tokens)


def search_tokens_from_text(raw: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z]+", " ", str(raw or "").lower())
    tokens: set[str] = set()
    for token in normalized.split():
        if not token:
            continue
        tokens.add(token)
        for chunk in re.findall(r"[a-z]+|\d+", token):
            if len(chunk) >= 2:
                tokens.add(chunk)
    return tokens


def query_token_in_haystack(query_token: str, haystack: str, search_tokens: set[str]) -> bool:
    if not query_token:
        return False
    if query_token in haystack:
        return True
    return query_token_hit(query_token, search_tokens, haystack)


def query_token_hit(query_token: str, search_tokens: set[str], haystack: str) -> bool:
    if not query_token:
        return False
    if query_token in search_tokens:
        return True

    for token in search_tokens:
        if query_token in token:
            return True
        if token in query_token:
            return True

    # Mixed alphanumeric filenames can still match broad query terms (e.g. blood -> bloodr).
    for chunk in re.findall(r"[a-z]+|\d+", query_token):
        if len(chunk) < 3:
            continue
        if chunk in search_tokens:
            return True
        for token in search_tokens:
            if chunk in token:
                return True
    return False


def tokenize_query(query: str) -> list[str]:
    text = re.sub(r"[^0-9a-zA-Z]+", " ", str(query or "").lower())
    return [token for token in text.split() if token and token not in QUERY_STOP_WORDS]


def query_matches(haystack: str, query: str) -> bool:
    normalized = str(haystack or "").lower()
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True
    if normalized_query in normalized:
        return True
    tokens = tokenize_query(normalized_query)
    if not tokens:
        return False
    haystack_tokens = search_tokens_from_text(normalized)
    return all(query_token_in_haystack(token, normalized, haystack_tokens) for token in tokens)


def query_matches_any(haystack: str, query: str) -> bool:
    normalized = str(haystack or "").lower()
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True
    if normalized_query in normalized:
        return True
    tokens = tokenize_query(normalized_query)
    if not tokens:
        return False
    haystack_tokens = search_tokens_from_text(normalized)
    return any(query_token_in_haystack(token, normalized, haystack_tokens) for token in tokens)


def metadata_from_mapping(payload: dict[str, Any]) -> str:
    preferred_keys = (
        "title",
        "name",
        "filename",
        "description",
        "desc",
        "summary",
        "author",
        "credits",
        "textfile",
        "theme",
        "genre",
        "keywords",
        "tags",
        "dir",
        "directory",
        "path",
        "id",
    )
    collected: list[Any] = []
    for key in preferred_keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (str, int, float)):
            collected.append(value)
        elif isinstance(value, (list, tuple, set)):
            collected.extend([item for item in value if isinstance(item, (str, int, float))])
    return normalize_metadata_text(*collected)


def result_search_blob(result: WadBrowserResult) -> str:
    return normalize_metadata_text(
        result.title,
        result.remote_path,
        result.description,
        result.metadata_text,
        result.source_name,
    ).lower()


def human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "—"
    units = [(1024**4, "TB"), (1024**3, "GB"), (1024**2, "MB"), (1024, "KB")]
    for divisor, name in units:
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.1f} {name}"
    return f"{size_bytes} B"


def short_age(seconds: float) -> str:
    if seconds <= 0:
        return "just now"
    minute = 60
    hour = minute * 60
    day = hour * 24
    if seconds < minute:
        return f"{int(seconds)}s ago"
    if seconds < hour:
        return f"{int(seconds / minute)}m ago"
    if seconds < day:
        return f"{int(seconds / hour)}h ago"
    return f"{int(seconds / day)}d ago"


def summarize_text(text: str, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return "No description available."
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[: max(0, limit - 3)].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return f"{clipped}..."
