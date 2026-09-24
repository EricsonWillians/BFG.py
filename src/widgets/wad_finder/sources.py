"""Source coercion, seeding and parsing helpers for the WAD finder (Qt-free).

Functions here accept and return plain dicts matching the persisted config
shape; the widget keeps working with dicts until a later refactor phase.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

from .constants import (
    DEFAULT_SOURCE_PARSER,
    DEFAULT_SOURCES,
    KNOWN_PARSERS,
    PARSER_ALIASES,
    SOURCE_STATUS_UNKNOWN,
    SOURCE_STATUSES,
)


def normalize_parser(raw: str) -> str:
    parser = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    if not parser:
        return DEFAULT_SOURCE_PARSER
    return PARSER_ALIASES.get(
        parser,
        parser if parser in KNOWN_PARSERS else DEFAULT_SOURCE_PARSER,
    )


def normalize_source_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")


def is_idgames_source(source: dict[str, str]) -> bool:
    base = str(source.get("base", "")).lower()
    browser = str(source.get("browser", "")).lower()
    return "idgames" in base or "idgames" in browser


def coerce_source(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status", SOURCE_STATUS_UNKNOWN)).lower().strip()
    if status not in SOURCE_STATUSES:
        status = SOURCE_STATUS_UNKNOWN

    source_id = normalize_source_id(
        str(raw.get("id", "")).strip()
        or str(raw.get("name", "")).strip()
        or str(raw.get("base", "")).strip()
        or "source"
    )
    name = str(raw.get("name", "")).strip() or source_id
    base = str(raw.get("base", "")).strip()
    if base and not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    index = str(raw.get("index", "fullsort.gz")).strip() or "fullsort.gz"
    browser = str(raw.get("browser", base)).strip() or base
    if browser and not browser.startswith(("http://", "https://")):
        browser = f"https://{browser}"
    return {
        "id": normalize_source_id(source_id),
        "name": name,
        "base": base.rstrip("/"),
        "index": index,
        "browser": browser.rstrip("/"),
        "parser": normalize_parser(raw.get("parser", DEFAULT_SOURCE_PARSER)),
        "enabled": bool(raw.get("enabled", True)),
        "status": status,
        "status_message": str(raw.get("status_message", "")).strip(),
        "status_checked_at": float(raw.get("status_checked_at", 0.0) or 0.0),
    }


def seed_sources(
    source_state: list[dict[str, Any]] | None,
    default_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the ordered, deduplicated list of coerced source dicts."""
    if default_sources is None:
        default_sources = DEFAULT_SOURCES

    defaults = [coerce_source(item) for item in default_sources]
    if source_state:
        defaults = []
        for item in source_state:
            if not isinstance(item, dict):
                continue
            defaults.append(coerce_source(item))
        if not defaults:
            defaults = [coerce_source(item) for item in default_sources]
        else:
            existing = {normalize_source_id(str(item.get("id", ""))) for item in defaults}
            for item in default_sources:
                source_id = normalize_source_id(item["id"])
                if source_id not in existing:
                    defaults.append(coerce_source(item))

    ordered: list[dict[str, Any]] = []
    seen = set()
    for entry in defaults:
        source_id = entry["id"]
        if not source_id or source_id in seen:
            continue
        if not entry["base"]:
            continue
        ordered.append(entry)
        seen.add(source_id)
    return ordered


def normalize_source_url(raw: str) -> str:
    parsed = urlparse(str(raw).strip())
    text = str(raw).strip()
    if not parsed.scheme:
        if text.startswith("//"):
            return f"https:{text}"
        if text:
            return f"https://{text}"
        return ""
    return parsed.geturl()


def make_host_name(source: str) -> str:
    parsed = urlparse(source)
    if parsed.netloc:
        if parsed.path and parsed.path.rstrip("/"):
            return f"{parsed.netloc} ({parsed.path.rstrip('/')})"
        return parsed.netloc
    return source


def infer_source_parser(source_url: str, index: str) -> str:
    source_text = str(source_url).lower().strip()
    index_text = str(index).lower().strip()

    if not source_text:
        return DEFAULT_SOURCE_PARSER

    if "/api.php" in source_text or "/api/" in source_text or index_text.endswith("api.php"):
        return "idgames_api"
    if any(token in source_text for token in ("/rss", ".rss", "/atom", ".atom", "/feed")):
        return "rss"
    if source_text.endswith((".json", ".jsn")) or index_text.endswith((".json", ".jsn", ".json.gz", ".jsn.gz")):
        return "json"
    if source_text.endswith(".txt") or index_text.endswith(".txt"):
        return "text"
    if any(token in source_text for token in ("/json", "type=json")):
        return "json"
    if "doomworld" in source_text and "idgames" in source_text:
        return "auto"
    if source_text.endswith((".gz", ".z", ".zip")):
        return "fullsort"
    if any(token in source_text for token in ("/files", "/pub/idgames", "/idgames/", "/download")):
        return "html"
    if source_text.endswith("/"):
        return "html"
    return "fullsort"


def default_index_for_parser(parser: str, index: str) -> str:
    parser = normalize_parser(parser)
    index = str(index).strip()
    if index:
        return index
    if parser == "idgames_api":
        return "api.php"
    if parser == "json":
        return "index.json"
    if parser == "rss":
        return "rss.xml"
    if parser == "text":
        return "index.txt"
    if parser == "html":
        return ""
    return "fullsort.gz"


def custom_source_id(url: str, name: str | None, base: str | None = None) -> str:
    source_base = normalize_source_id(base or url)
    if not source_base and name:
        source_base = normalize_source_id(name)
    if not source_base:
        source_base = normalize_source_id(urlparse(url).netloc or urlparse(url).path or "source")
    return f"custom:{source_base}"


def allocate_source_id(source_id: str, existing_ids) -> str:
    source_id = normalize_source_id(source_id)
    if not source_id:
        source_id = "source"

    candidate = source_id
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{source_id}-{suffix}"
        suffix += 1
    return candidate


def _coerce_json_source(payload: dict[str, Any]) -> dict[str, str] | None:
    source = payload.get("url") or payload.get("base") or payload.get("source") or payload.get("source_url")
    if not source:
        return None
    source = normalize_source_url(str(source))
    if not source:
        return None

    provided_index = str(payload.get("index", "")).strip()
    parser_raw = str(payload.get("parser", payload.get("mode", ""))).strip()
    # normalize_parser never returns an empty string, so no fallback needed.
    parser = (
        normalize_parser(parser_raw)
        if parser_raw
        else normalize_parser(infer_source_parser(source, provided_index))
    )

    return {
        "name": str(payload.get("name", "")).strip() or make_host_name(source),
        "url": source,
        "index": default_index_for_parser(parser, provided_index),
        "parser": parser,
        "base": source,
    }


def _looks_like_key_value(raw_value: str) -> bool:
    def _has_known_key(chunk_value: str) -> bool:
        if "=" not in chunk_value:
            return False
        key = chunk_value.split("=", 1)[0].strip().lower()
        return key in {"url", "base", "source", "source_url", "name", "index", "parser", "mode"}

    if raw_value.startswith(("http://", "https://", "ftp://")):
        if "|" not in raw_value:
            return False
        for chunk in raw_value.split("|"):
            if _has_known_key(chunk.strip()):
                return True
        return False
    if raw_value.startswith("{") and raw_value.endswith("}"):
        return False
    if raw_value.startswith("[") and raw_value.endswith("]"):
        return False

    if "|" in raw_value:
        return any("=" in chunk.strip() for chunk in raw_value.split("|"))

    return bool(
        re.match(
            r"(?i)^\s*(?:url|base|source|source_url|name|index|parser|mode)\s*=",
            raw_value.strip(),
        )
    )


def parse_custom_source_entry(raw: str) -> dict[str, str] | None:
    if not raw:
        return None

    raw = raw.strip()
    if raw.startswith("#"):
        return None

    if _looks_like_key_value(raw):
        if raw.startswith("{") and raw.endswith("}"):
            with suppress(Exception):
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    parsed = _coerce_json_source(payload)
                    if parsed is not None:
                        return parsed

        config = {}
        for chunk in [part.strip() for part in raw.split("|")]:
            if not chunk:
                continue
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                config[str(key).strip().lower()] = str(value).strip()
        if not config:
            return None

        source = config.get("url") or config.get("base") or config.get("source") or config.get("source_url")
        if not source:
            return None

        source = normalize_source_url(source)
        if not source:
            return None

        index = config.get("index", "").strip()
        parser_raw = str(config.get("parser", config.get("mode", ""))).strip()
        parser = normalize_parser(parser_raw) if parser_raw else normalize_parser(
            infer_source_parser(source, index)
        )
        name = config.get("name") or make_host_name(source)
        index = default_index_for_parser(parser, index)
        return {
            "name": name,
            "url": source,
            "index": index,
            "parser": parser,
            "base": source,
        }

    if raw.startswith("{") and raw.endswith("}"):
        with suppress(Exception):
            payload = json.loads(raw)
            if isinstance(payload, dict):
                parsed = _coerce_json_source(payload)
                if parsed is not None:
                    return parsed

    parts = [chunk.strip() for chunk in re.split(r"[|,]", raw)]
    parts = [part for part in parts if part]
    if not parts:
        return None

    parser = "fullsort"
    index = "fullsort.gz"
    name: str | None = None
    parser_explicit = False

    if len(parts) == 1:
        source = parts[0]
    elif len(parts) == 2:
        if parts[0].startswith("http://") or parts[0].startswith("https://"):
            source = parts[0]
            index = parts[1]
        else:
            name = parts[0]
            source = parts[1]
    else:
        if parts[0].startswith("http://") or parts[0].startswith("https://"):
            source = parts[0]
            index = parts[1] if len(parts) > 1 else index
            parser = parts[2] if len(parts) > 2 else parser
            parser_explicit = len(parts) > 2 and bool(str(parts[2]).strip())
        else:
            name = parts[0]
            source = parts[1]
            if len(parts) > 2:
                index = parts[2]
            if len(parts) > 3:
                parser = parts[3]
                parser_explicit = bool(str(parts[3]).strip())

    source = normalize_source_url(source)
    if not source:
        return None

    if not index:
        index = ""
    parser = normalize_parser(parser if parser_explicit else infer_source_parser(source, index))
    index = default_index_for_parser(parser, index)

    return {
        "name": name or make_host_name(source),
        "url": source,
        "index": index.strip(),
        "parser": parser,
        "base": source,
    }
