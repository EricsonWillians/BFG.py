"""Pure index parsers for the WAD finder (Qt-free, no network I/O).

These were staticmethods on the search worker; they are extracted here so they
can be unit-tested without Qt or network access. ``crawl_html`` takes a
``fetch`` callable so the fetching policy stays with the caller.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import deque
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

from .constants import HTML_CRAWL_MAX_DEPTH, HTML_CRAWL_MAX_DIRS, HTML_CRAWL_MAX_FILES
from .models import WadBrowserResult
from .text import (
    looks_like_mod_file,
    metadata_from_mapping,
    normalize_metadata_text,
    normalize_remote_path,
    query_matches,
    query_matches_any,
    result_search_blob,
    tokenize_query,
)


def coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        text = str(value).strip().replace(",", "").replace(" ", "")
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def normalize_api_path(value: str) -> str:
    if not value:
        return ""
    path = str(value).strip().replace("\\", "/").split("?")[0].split("#")[0]
    return normalize_remote_path(path)


def collect_api_items(payload: Any) -> list[dict[str, Any]]:
    if not payload:
        return []

    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidate_keys = {
            "file",
            "files",
            "entries",
            "results",
            "content",
            "items",
            "data",
        }
        direct = False
        for key in ("filename", "file", "path", "title", "name", "id"):
            if key in payload and isinstance(payload[key], (str, int, float)):
                direct = True
                break
        if direct:
            candidates.append(payload)
        for key in candidate_keys:
            if key in payload:
                candidates.extend(collect_api_items(payload[key]))
        return candidates

    if isinstance(payload, list):
        for entry in payload:
            candidates.extend(collect_api_items(entry))
        return candidates

    return []


def parse_idgames_api(
    source: dict[str, str],
    text: str,
    query: str,
    allow_empty_query: bool = False,
    filter_by_query: bool = True,
) -> list[WadBrowserResult]:
    query = query.lower().strip()
    if not query and not allow_empty_query:
        return []

    try:
        payload = json.loads(text)
    except Exception:
        return []

    entries = collect_api_items(payload)
    parsed: list[WadBrowserResult] = []
    seen = set()

    for item in entries:
        if not isinstance(item, dict):
            continue
        filename = (
            str(item.get("filename") or item.get("file") or item.get("name") or item.get("title") or "")
            .strip()
        )
        if not filename:
            continue

        if not looks_like_mod_file(filename):
            continue

        path = normalize_api_path(
            str(item.get("path") or item.get("dir") or item.get("directory") or "")
        )
        # Strip a trailing slash from the directory so the join below never
        # produces a double-slash remote path.
        remote_path = normalize_remote_path(f"{path.rstrip('/')}/{filename}") if path else normalize_remote_path(filename)
        if not remote_path:
            continue
        if remote_path in seen:
            continue

        metadata_text = metadata_from_mapping(item)
        haystack = normalize_metadata_text(filename, path, metadata_text).lower()
        if filter_by_query and query and not query_matches_any(haystack, query):
            continue

        size = 0
        for size_key in ("size", "size_bytes", "filesize", "length"):
            if item.get(size_key) not in (None, ""):
                size = coerce_int(item[size_key])
                break

        description = ""
        for key in ("description", "desc", "summary", "author"):
            candidate = str(item.get(key, "")).strip()
            if candidate:
                description = candidate
                if description and key == "author":
                    description = f"Author: {description}"
                break
        download_value = (
            item.get("download")
            or item.get("download_url")
            or item.get("url")
            or item.get("file")
            or ""
        )
        browser_value = item.get("url") or item.get("page") or item.get("path") or ""

        download_url = str(download_value).strip()
        if download_url:
            if download_url.lower().startswith("http"):
                pass
            elif download_url.startswith("/"):
                download_url = f"{source['base'].rstrip('/')}{download_url}"
            else:
                download_url = urljoin(f"{source['base'].rstrip('/')}/", download_url)
        else:
            download_url = urljoin(f"{source['base'].rstrip('/')}/", remote_path)

        browser_url = str(browser_value).strip()
        if not browser_url.lower().startswith("http"):
            browser_url = urljoin(f"{source['browser'].rstrip('/')}/", remote_path)

        if not looks_like_mod_file(normalize_api_path(download_url)):
            if remote_path:
                download_url = urljoin(f"{source['base'].rstrip('/')}/", remote_path)

        parsed.append(
            WadBrowserResult(
                title=filename,
                description=description,
                metadata_text=metadata_text,
                source_id=source.get("id", ""),
                source_name=source.get("name", source.get("base", "")),
                size_bytes=size,
                remote_path=remote_path,
                download_url=download_url,
                browser_url=browser_url,
            )
        )
        seen.add(remote_path)

    if parsed:
        return parsed

    # Best-effort fallback for undocumented API payload shapes.
    fallback_paths: list[str] = []
    for key in ("path", "base", "file", "result", "download", "url"):
        if isinstance(payload, dict) and key in payload:
            fallback_paths.append(str(payload[key]))
    for entry in fallback_paths:
        normalized = normalize_api_path(entry)
        if not normalized or not looks_like_mod_file(normalized):
            continue
        parsed.append(
            WadBrowserResult(
                title=Path(normalized).name,
                description="",
                metadata_text="",
                source_id=source.get("id", ""),
                source_name=source.get("name", source.get("base", "")),
                size_bytes=0,
                remote_path=normalized,
                download_url=urljoin(f"{source['base'].rstrip('/')}/", normalized),
                browser_url=urljoin(f"{source['browser'].rstrip('/')}/", normalized),
            )
        )
    return parsed


def parse_json(source: dict[str, str], text: str, query: str) -> list[WadBrowserResult]:
    return parse_idgames_api(source, text, query, allow_empty_query=True)


def parse_rss(source: dict[str, str], text: str, query: str) -> list[WadBrowserResult]:
    query = query.lower().strip()
    if not text:
        return []

    try:
        root = DET.fromstring(text)
    except (ET.ParseError, DefusedXmlException):
        return []

    namespace = "{http://www.w3.org/2005/Atom}"
    items: list[ET.Element] = []
    if root.tag.startswith(namespace):
        items.extend(root.findall(f"{namespace}entry"))
    items.extend(root.findall(".//item"))
    if not items and root.tag.endswith("entry"):
        items.append(root)

    parsed: list[WadBrowserResult] = []
    seen = set()

    def _pick_text(node: ET.Element, keys: list[str]) -> str:
        for key in keys:
            target = node.find(key)
            if target is not None and (target.text or "").strip():
                return str(target.text).strip()
            if target is None and "}" in key:
                alt_key = key.split("}")[-1]
                target = node.find(alt_key)
                if target is not None and (target.text or "").strip():
                    return str(target.text).strip()
        return ""

    def _pick_attr(node: ET.Element, key: str, attr: str) -> str:
        target = node.find(key)
        if target is not None:
            value = target.attrib.get(attr, "").strip()
            if value:
                return value
        return ""

    for item in items:
        title = _pick_text(item, ["title", f"{namespace}title"])
        link = _pick_text(item, [f"{namespace}link", "link"])
        if not link:
            # Atom entries commonly carry an href-only <link href="..."/>;
            # read the attribute instead of the (unsupported) "link/@href"
            # ElementPath, which raises KeyError on href-only links.
            link = _pick_attr(item, f"{namespace}link", "href") or _pick_attr(item, "link", "href")

        if not link:
            link = _pick_text(item, ["guid", "id", "enclosure"])

        if not link and not title:
            continue

        description = _pick_text(item, ["description", "content", "summary", f"{namespace}summary"]) or title
        download_url = link.strip()
        if download_url.startswith("//"):
            download_url = f"https:{download_url}"
        if download_url and not download_url.startswith(("http://", "https://")):
            download_url = urljoin(f"{source['base'].rstrip('/')}/", download_url)

        remote = normalize_api_path(urlparse(download_url).path if download_url else "")
        if not remote:
            remote = normalize_api_path(_pick_text(item, ["enclosure" ]))
        if not remote:
            remote = normalize_api_path(title)

        if not remote or not looks_like_mod_file(remote):
            continue

        metadata_text = normalize_metadata_text(
            title,
            description,
            _pick_text(item, ["author", f"{namespace}author"]),
            [category.text.strip() for category in item.findall("category") if (category.text or "").strip()],
        )
        haystack = normalize_metadata_text(remote, metadata_text).lower()
        if query and not query_matches_any(haystack, query):
            continue

        browser_url = _pick_text(item, ["link", f"{namespace}link", "guid", "id"])
        if not browser_url:
            browser_url = download_url
        elif browser_url.startswith("//"):
            browser_url = f"https:{browser_url}"
        if browser_url and not browser_url.startswith(("http://", "https://")):
            browser_url = urljoin(f"{source['browser'].rstrip('/')}/", browser_url)

        key = f"{source.get('id', '')}:{remote.lower()}"
        if key in seen:
            continue
        seen.add(key)

        size = 0
        for size_key in ("length", "size", "content_length", "filesize"):
            size_attr = item.find(f"enclosure")
            if size_attr is not None and size_attr.attrib.get(size_key, ""):
                size = coerce_int(size_attr.attrib.get(size_key, 0))
                break

        parsed.append(
            WadBrowserResult(
                title=title or Path(remote).name,
                description=description,
                metadata_text=metadata_text,
                source_id=source.get("id", ""),
                source_name=source.get("name", source.get("base", "")),
                size_bytes=size,
                remote_path=remote,
                download_url=download_url,
                browser_url=browser_url,
            )
        )

    return parsed


def iter_html_links(html: str) -> Iterable[tuple[str, str]]:
    for match in re.finditer(
        r"<a[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        html,
        flags=re.I | re.S,
    ):
        href = match.group(1).strip()
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(("javascript:", "mailto:")):
            continue
        label = re.sub(r"<[^>]+>", "", match.group(2))
        label = unescape(label).strip()
        yield href, label


def link_targets(html: str) -> Iterable[str]:
    for href, _ in iter_html_links(html):
        yield href


def relative_path_for(base_url: str, candidate_url: str) -> str:
    base_parts = urlparse(base_url)
    candidate = urlparse(candidate_url)
    if candidate.scheme != base_parts.scheme or candidate.netloc != base_parts.netloc:
        return ""
    base_path = base_parts.path.rstrip("/").lstrip("/")
    raw = candidate.path
    if raw.endswith("/"):
        raw = raw[:-1]
    raw = raw.lstrip("/")
    if base_path and raw.startswith(base_path + "/"):
        raw = raw[len(base_path) + 1:]
    elif base_path and raw == base_path:
        raw = ""
    return raw


def parse_html(
    source: dict[str, str], text: str, query: str
) -> list[tuple[int, str, str]]:
    query = query.lower().strip()
    found = []
    seen = set()
    base = source["base"].rstrip("/")
    base_path = urlparse(base).path.rstrip("/")
    for href, label in iter_html_links(text):
        if href in ("../", "./"):
            continue
        absolute = urljoin(base + "/", href)
        if not absolute.startswith(base):
            continue
        rel = relative_path_for(base, absolute)
        if not rel:
            continue
        rel = rel.lstrip("/")
        rel = normalize_remote_path(rel)
        if rel in seen:
            continue
        if rel.endswith("/"):
            continue
        if rel.lower().endswith("/"):
            continue
        if not looks_like_mod_file(rel):
            continue
        if query:
            search_blob = f"{rel} {label}".lower().replace("_", " ")
            if not query_matches_any(search_blob, query):
                continue
        seen.add(rel)
        found.append((0, rel, label[:140]))
        if len(found) >= HTML_CRAWL_MAX_FILES:
            break
    return found


def crawl_html(
    source: dict[str, str],
    start_path: str,
    query: str,
    fetch: Callable[[str], str],
) -> list[tuple[int, str, str]]:
    query = query.lower().strip()
    base = source["base"].rstrip("/")
    seen_dirs = set()
    discovered = set()
    results: list[tuple[int, str, str]] = []
    page = ""

    queue = deque([(start_path, 0)])
    while queue and len(seen_dirs) < HTML_CRAWL_MAX_DIRS and len(results) < HTML_CRAWL_MAX_FILES:
        rel_dir, depth = queue.popleft()
        rel_dir = rel_dir.strip().lstrip("/")
        current = f"{base}/"
        if rel_dir:
            current += f"{rel_dir.rstrip('/')}/"
        try:
            page = fetch(current)
        except Exception:
            continue

        if rel_dir not in seen_dirs:
            seen_dirs.add(rel_dir)

        for href, label in iter_html_links(page):
            if not href or href.startswith("#"):
                continue
            if href.lower().startswith(("javascript:", "mailto:")):
                continue
            absolute = urljoin(current, href)
            rel = relative_path_for(base, absolute).strip().lstrip("/")
            if not rel:
                continue
            if rel in discovered:
                continue

            if href.endswith("/") or absolute.endswith("/"):
                if depth + 1 < HTML_CRAWL_MAX_DEPTH:
                    discovered.add(rel)
                    queue.append((rel, depth + 1))
                continue

            if not looks_like_mod_file(rel):
                continue
            if query and not query_matches_any(f"{rel} {label}", query):
                continue
            discovered.add(rel)
            results.append((0, rel, label[:140]))
            if len(results) >= HTML_CRAWL_MAX_FILES:
                break

    if not query and not results and page:
        for parsed in parse_html(source, page, query):
            if parsed:
                results.append(parsed)
    return results


def crawl_start_path(start: str) -> str:
    crawl_start = str(start or "").strip()
    if crawl_start.lower().endswith(".php"):
        return ""
    if crawl_start.lower().endswith((".gz", ".zip", ".txt", ".tgz")):
        return ""
    return crawl_start


def looks_like_datetime_token(raw: str) -> bool:
    token = (raw or "").strip()
    if not token:
        return False
    if re.fullmatch(r"\d{4}[/-]\d{2}[/-]\d{2}", token):
        return True
    if re.fullmatch(r"\d{8}", token):
        return True
    if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", token):
        return True
    return False


def parse_fullsort_tokens(tokens: list[str]) -> tuple[int, str, str] | None:
    if not tokens:
        return None

    # Common formats observed across idgames mirrors vary:
    #  - date size path desc...
    #  - size path desc...
    #  - date time size path desc...
    idx = 0
    if looks_like_datetime_token(tokens[idx]):
        idx += 1
        if idx < len(tokens) and looks_like_datetime_token(tokens[idx]):
            idx += 1

    while idx < len(tokens):
        token = tokens[idx].replace(",", "").strip()
        if re.fullmatch(r"\d+(?:\.\d+)?(?:[kmgt](?:i?b?)?)?", token, re.I):
            break
        idx += 1

    if idx >= len(tokens):
        return None
    size_token = tokens[idx]
    if not size_token:
        return None

    size_value = size_token.replace(",", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmgt]?)(?:i?b?)?", size_value, re.I)
    if not match:
        return None

    size_factor = {
        "": 1.0,
        "k": 1024.0,
        "m": 1024.0 ** 2,
        "g": 1024.0 ** 3,
        "t": 1024.0 ** 4,
    }.get(match.group(2).lower(), 1.0)

    try:
        size = int(float(match.group(1)) * size_factor)
    except (TypeError, ValueError):
        return None
    path_index = idx + 1
    if path_index >= len(tokens):
        return None

    remote_path = normalize_remote_path(tokens[path_index])
    description = " ".join(tokens[path_index + 1 :]).strip()
    return size, remote_path, description


def parse_fullsort(text: str) -> list[tuple[int, str, str]]:
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("fullpath") or line.startswith("Control switch"):
            continue
        if line.startswith("#") or line.startswith(";"):
            continue

        parsed = parse_fullsort_tokens(line.split())
        if not parsed:
            continue
        size, path, description = parsed
        if not path:
            continue
        if not looks_like_mod_file(path):
            continue
        entries.append((size, path, description))
    return entries


def parse_text(text: str, source: dict[str, str], query: str) -> list[tuple[int, str, str]]:
    query = query.lower().strip()
    base = source["base"].rstrip("/")
    browser = source["browser"].rstrip("/")
    if not base:
        return []

    entries: list[tuple[int, str, str]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("#") or raw.startswith(";"):
            continue

        parts = raw.split()
        if not parts:
            continue

        candidate = ""
        size = 0
        if looks_like_mod_file(normalize_remote_path(parts[0])):
            candidate = parts[0]
            tail = " ".join(parts[1:])
        elif len(parts) > 1 and looks_like_mod_file(normalize_remote_path(parts[1])):
            parsed_size = coerce_int(parts[0])
            if parsed_size:
                size = parsed_size
            candidate = parts[1]
            tail = " ".join(parts[2:])
        else:
            remainder = None
            for part in parts:
                normalized = normalize_remote_path(part)
                if looks_like_mod_file(normalized):
                    candidate = part
                    remainder = raw
                    break
            if remainder is None:
                continue
            tail = remainder

        remote = normalize_remote_path(candidate)
        if not remote or not looks_like_mod_file(remote):
            continue

        haystack = f"{raw} {tail} {candidate}".lower()
        if query and not query_matches_any(haystack, query):
            continue

        if remote.startswith(base):
            download_url = remote
            browser_url = remote
        else:
            download_url = urljoin(f"{base}/", remote)
            browser_url = urljoin(f"{browser}/", remote)

        entries.append((size, remote, tail.strip()))

    return entries


def candidate_textfile_urls(source: dict[str, str], result: WadBrowserResult) -> list[str]:
    base = str(source.get("base", "")).strip().rstrip("/")
    if not base:
        return []

    remote = normalize_remote_path(result.remote_path)
    if not remote:
        return []

    candidates: list[str] = []
    path = Path(remote)

    primary = urljoin(f"{base}/", str(path.with_suffix(".txt")))
    candidates.append(primary)

    stem = path.name
    if stem.startswith("#") and len(stem) > 1:
        trimmed = path.with_name(stem[1:])
        candidates.append(urljoin(f"{base}/", str(trimmed.with_suffix(".txt"))))
        candidates.append(urljoin(f"{base}/", str(trimmed)))

    fallback = str(path)
    if fallback != fallback.replace(".zip", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".zip", ".txt")))
    if fallback != fallback.replace(".wad", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".wad", ".txt")))
    if fallback != fallback.replace(".pk3", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".pk3", ".txt")))
    if fallback != fallback.replace(".ipk3", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".ipk3", ".txt")))
    if fallback != fallback.replace(".pk7", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".pk7", ".txt")))
    if fallback != fallback.replace(".pke", ".txt"):
        candidates.append(urljoin(f"{base}/", fallback.replace(".pke", ".txt")))

    # Include a browser-side URL variant to allow mirrors with different file layouts.
    browser = str(result.browser_url or "").strip()
    if browser and browser.lower().startswith("http"):
        parsed = urlparse(browser)
        if parsed.path:
            path_only = parsed._replace(query="", fragment="").geturl()
            browser_path = urlparse(path_only).path
            if browser_path:
                candidates.append(urljoin(f"{base}/", str(Path(browser_path).with_suffix(".txt")).lstrip("/")))
                candidates.append(path_only)

    deduped: list[str] = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def parse_textfile_metadata(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not text:
        return fields

    lines = text.replace("\r", "\n").splitlines()
    description_lines: list[str] = []
    parsing_description = False

    for line in lines:
        raw = line.strip()
        if not raw:
            if parsing_description:
                continue
            continue

        if re.fullmatch(r"[=*\-]{4,}", raw):
            if parsing_description and description_lines:
                break
            continue

        if parsing_description:
            if raw.startswith("*") and ":" in raw:
                break
            if re.match(r"^[A-Za-z][A-Za-z0-9 _/()'\".#!,:-]{1,140}:\s*.+", raw):
                break
            description_lines.append(raw)
            continue

        match = re.match(
            r"^([A-Za-z][A-Za-z0-9 _/()'\".#!,:-]{1,140}):\s*(.*)$",
            raw,
        )
        if not match:
            continue

        key = re.sub(r"\s+", " ", match.group(1).strip()).strip().lower()
        value = match.group(2).strip()
        if key == "description":
            parsing_description = True
            if value:
                description_lines.append(value)
            continue

        if not value:
            continue

        fields[key] = value

    if description_lines:
        fields["description"] = normalize_metadata_text(*description_lines)

    return fields


def textfile_enrichment_candidates(
    query: str, results: list[WadBrowserResult]
) -> list[WadBrowserResult]:
    """Rank results most likely to gain from textfile metadata (pure scoring)."""
    tokens = tokenize_query(query)

    def score(item: WadBrowserResult) -> int:
        blob = result_search_blob(item)
        return sum(1 for token in tokens if token in blob)

    candidate_items: list[WadBrowserResult] = []
    for item in results:
        haystack = result_search_blob(item)
        if query_matches_any(haystack, query):
            candidate_items.append(item)
            continue
        if tokens and any(token in item.title.lower() for token in tokens):
            candidate_items.append(item)
            continue
        if tokens and any(token in item.remote_path.lower() for token in tokens):
            candidate_items.append(item)

    if not candidate_items:
        candidate_items = sorted(results, key=lambda item: item.size_bytes, reverse=True)

    return sorted(
        candidate_items,
        key=lambda item: (
            -score(item),
            item.remote_path.lower(),
            item.size_bytes,
            item.title.lower(),
        ),
    )


def apply_textfile_metadata(item: WadBrowserResult, payload: dict[str, str]) -> bool:
    """Merge parsed textfile fields into a result in place. Returns False when
    the payload is empty (caller should move on to the next candidate URL)."""
    if not payload:
        return False
    payload = dict(payload)
    description = payload.pop("description", "")
    if description:
        item.description = normalize_metadata_text(item.description, description)
    if payload:
        item.metadata_text = normalize_metadata_text(item.metadata_text, description, *payload.values())
        item.metadata_text = item.metadata_text[:1800]
    return True
