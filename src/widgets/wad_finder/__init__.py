from __future__ import annotations

import gzip
import json
import os
from html import unescape
import re
import time
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config import DEFAULT_BROWSER_SOURCES as CONFIG_DEFAULT_SOURCES

UA = "BFG.py wad browser/2.0 (+https://github.com)"
DEFAULT_EXTENSIONS = (".wad", ".pk3", ".ipk3", ".pk7", ".pke", ".zip")
INDEX_TTL_SECONDS = 60 * 60 * 6
DEFAULT_SOURCE_LIMIT = 750
MAX_CACHED_INDEX_ENTRIES = 5000
SOURCE_STATUS_UNKNOWN = "unknown"
SOURCE_STATUS_CHECKING = "checking"
SOURCE_STATUS_CACHED = "cached"
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_UNREACHABLE = "unreachable"
SOURCE_STATUS_ERROR = "error"
SOURCE_STATUS_DISABLED = "disabled"
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

DEFAULT_SOURCE_PARSER = "fullsort"


def _normalize_parser(raw: str) -> str:
    parser = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    if not parser:
        return DEFAULT_SOURCE_PARSER
    return PARSER_ALIASES.get(
        parser,
        parser
        if parser in {"fullsort", "html", "auto", "idgames_api", "json", "rss", "text"}
        else DEFAULT_SOURCE_PARSER,
    )


def _coerce_parser(raw: str) -> str:
    return _normalize_parser(raw)

DEFAULT_SOURCES = [
    source.to_dict() if hasattr(source, "to_dict") else dict(source)
    for source in CONFIG_DEFAULT_SOURCES
]


@dataclass(frozen=True)
class WadBrowserResult:
    title: str
    description: str
    source_id: str
    source_name: str
    size_bytes: int
    remote_path: str
    download_url: str
    browser_url: str


@dataclass
class _IndexCacheEntry:
    source_id: str
    source_name: str
    fetched_at: float
    entries: List[WadBrowserResult]


class _SearchSignals(QObject):
    finished = pyqtSignal(int, str, list)
    failed = pyqtSignal(int, str, str)


class _DownloadSignals(QObject):
    progress = pyqtSignal(int, int, int, str)
    finished = pyqtSignal(int, str, str)
    failed = pyqtSignal(int, str, str)


class _DiscoverSignals(QObject):
    finished = pyqtSignal(int, list)
    failed = pyqtSignal(int, str)


class _DiscoverSourcesWorker(QRunnable):
    def __init__(self, token: int, seed_urls: Optional[List[str]] = None):
        super().__init__()
        self.token = token
        self.seeds = list(seed_urls) if seed_urls else list(MIRRORS_DISCOVERY_URLS)
        self.signals = _DiscoverSignals()

    @staticmethod
    def _coerce_mirror_base(raw: str) -> str:
        raw = str(raw).strip()
        if not raw:
            return ""

        if raw.startswith("//"):
            raw = f"https:{raw}"

        parsed = urlparse(raw)
        if not parsed.scheme:
            return ""
        if parsed.scheme not in {"http", "https"}:
            return ""
        if not parsed.netloc:
            return ""

        low_netloc = parsed.netloc.lower()
        low_path = (parsed.path or "").lower()
        if "idgames" not in low_netloc and "idgames" not in low_path:
            return ""
        if "idgames2" in low_path or "/incoming" in low_path:
            return ""

        path = parsed.path
        match = re.search(r"(^|/)idgames(?:/|$)", path.lower())
        if not match:
            return ""
        path = path[: match.end()]

        rebuilt = parsed._replace(path=path.rstrip("/"), query="", fragment="")
        return rebuilt.geturl().rstrip("/")

    @staticmethod
    def _flatten_possible_strings(payload: Any, seen: Optional[set] = None) -> List[str]:
        collected: List[str] = []
        if seen is None:
            seen = set()

        if isinstance(payload, str):
            value = payload.strip()
            if value and value.lower() not in seen:
                collected.append(value)
                seen.add(value.lower())
            return collected

        if isinstance(payload, dict):
            for value in payload.values():
                collected.extend(_DiscoverSourcesWorker._flatten_possible_strings(value, seen=seen))
            return collected

        if isinstance(payload, list):
            for value in payload:
                collected.extend(_DiscoverSourcesWorker._flatten_possible_strings(value, seen=seen))
            return collected

        return collected

    @staticmethod
    def _parse_mirror_api_payload(payload: Any) -> List[str]:
        text_candidates: List[str] = []
        discovered: List[str] = []
        for candidate in _DiscoverSourcesWorker._flatten_possible_strings(payload):
            normalized = _DiscoverSourcesWorker._coerce_mirror_base(candidate)
            if normalized:
                text_candidates.append(normalized)

        if not text_candidates and isinstance(payload, dict):
            preferred_keys = {"url", "mirror", "base", "site", "host", "origin", "path"}
            for key in preferred_keys:
                value = payload.get(key)
                if isinstance(value, (list, tuple, set)):
                    for entry in value:
                        normalized = _DiscoverSourcesWorker._coerce_mirror_base(str(entry))
                        if normalized:
                            discovered.append(normalized)
                elif isinstance(value, str):
                    normalized = _DiscoverSourcesWorker._coerce_mirror_base(value)
                    if normalized:
                        discovered.append(normalized)
            if discovered:
                return discovered

        for item in text_candidates:
            normalized = _DiscoverSourcesWorker._coerce_mirror_base(item)
            if normalized:
                discovered.append(normalized)
        return discovered

    @staticmethod
    def _discover_from_api(seed_url: str) -> List[dict]:
        discovered = []
        try:
            response = requests.get(
                seed_url,
                headers={"User-Agent": UA},
                timeout=IDGAMES_API_TIMEOUT,
            )
            response.raise_for_status()
        except Exception:
            return discovered

        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = None

        mirrors: List[str] = []
        if payload is not None:
            mirrors = _DiscoverSourcesWorker._parse_mirror_api_payload(payload)

        if not mirrors:
            mirrors = _DiscoverSourcesWorker._extract_plain_mirrors(response.text)

        for base in mirrors:
            discovered.append(
                {
                    "base": base,
                    "browser": f"{base}/",
                    "index": "fullsort.gz",
                    "name": _DiscoverSourcesWorker._discover_name(base),
                    "enabled": True,
                    "parser": "fullsort",
                }
            )
        return discovered

    @staticmethod
    def _extract_plain_mirrors(text: str) -> List[str]:
        candidates = re.findall(r"""https?://[^\s<>\"']+/[^\s<>\"']*idgames[^\s<>\"']*""", text, flags=re.I)
        normalized = []
        for entry in candidates:
            clean = _DiscoverSourcesWorker._coerce_mirror_base(entry)
            if clean:
                normalized.append(clean)
        return normalized

    @staticmethod
    def _extract_idgames_mirrors(html: str, page_url: str) -> List[str]:
        matches = re.findall(r"""href=[\"']([^\"']+)[\"']""", html, flags=re.I)
        plain_urls = re.findall(r"""(?:(?:https?:)?//[^\"'>\s]+/[^\"'>\s]+)""", html)
        plain_urls.extend(re.findall(r"""https?://[^\s<>\"']+/[^\s<>\"']*idgames[^\s<>\"']*""", html, flags=re.I))
        bases = []
        seen = set()
        page_base = page_url
        if not page_base.endswith("/"):
            parsed = urlparse(page_base)
            if parsed.path and not parsed.path.endswith("/"):
                page_base = page_url[: page_url.rfind("/") + 1]

        for raw in matches:
            if not raw:
                continue
            normalized = _DiscoverSourcesWorker._coerce_mirror_base(raw)
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            bases.append(normalized)

        for raw in plain_urls:
            normalized = _DiscoverSourcesWorker._coerce_mirror_base(raw)
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            bases.append(normalized)

        return bases

    @staticmethod
    def _head_probe_index(base: str) -> tuple[str, bool]:
        base = base.rstrip("/")
        index_candidates = ("fullsort.gz", "fullsort")
        for candidate in index_candidates:
            url = f"{base}/{candidate}"
            for method in ("head", "get"):
                response = None
                try:
                    response = requests.request(
                        method.upper(),
                        url,
                        headers={"User-Agent": UA},
                        timeout=SOURCE_PING_TIMEOUT,
                        stream=method == "get",
                    )
                    if response.status_code < 200 or response.status_code >= 400:
                        continue
                    ctype = (response.headers.get("content-type") or "").lower()
                    if ctype.startswith("text/html") and "x-gzip" not in ctype:
                        continue
                    if method == "get":
                        # Do not download full index here; read a tiny chunk for validation.
                        response.close()
                    return candidate, True
                except requests.RequestException:
                    continue
                finally:
                    try:
                        if response is not None:
                            response.close()
                    except Exception:
                        pass
        return "fullsort.gz", False

    @staticmethod
    def _discover_name(url: str) -> str:
        parsed = urlparse(url)
        suffix = parsed.path.rstrip("/")
        if suffix and suffix.lower() != "/idgames":
            suffix = f"{suffix}"
        return f"{parsed.netloc} ({suffix})" if suffix else parsed.netloc

    def run(self):
        discovered: List[dict] = []
        errors: List[str] = []

        for endpoint in MIRRORS_DISCOVERY_API_URLS:
            discovered.extend(self._discover_from_api(endpoint))

        seed_urls = self.seeds or list(MIRRORS_DISCOVERY_URLS)
        queue = deque([(str(url), 0) for url in seed_urls if str(url).strip()])
        seen_pages = set()
        emitted = 0

        while queue:
            discovery_url, depth = queue.popleft()
            if discovery_url in seen_pages:
                continue
            seen_pages.add(discovery_url)

            try:
                response = requests.get(
                    discovery_url,
                    headers={"User-Agent": UA},
                    timeout=MIRRORS_DISCOVERY_TIMEOUT,
                )
                response.raise_for_status()
            except Exception as exc:
                errors.append(str(exc))
                continue

            html = response.text
            for base in self._extract_idgames_mirrors(html, discovery_url):
                discovered_index, reachable = self._head_probe_index(base)
                discovered.append(
                    {
                        "base": base,
                        "browser": f"{base}/",
                        "index": discovered_index,
                        "name": self._discover_name(base),
                        "enabled": bool(reachable),
                        "parser": "auto",
                    }
                )

            if depth >= MIRRORS_DISCOVERY_MAX_DEPTH:
                continue

            for href in re.findall(r"""href=[\"']([^\"']+)[\"']""", html, flags=re.I):
                if not href:
                    continue
                low = href.lower()
                if low.startswith(("mailto:", "javascript:")):
                    continue
                if low.startswith("#"):
                    continue

                if href.startswith("//"):
                    href = f"https:{href}"
                elif href.startswith("/"):
                    href = urljoin(discovery_url, href)
                elif not href.startswith("http://") and not href.startswith("https://"):
                    continue

                if any(mark in low for mark in ("idgames", "doomworld.com", "pub/idgames")):
                    queue.append((href, depth + 1))

            if emitted > MIRRORS_DISCOVERY_MAX_PAGES:
                break
            emitted += 1

        if discovered:
            deduped = []
            seen = set()
            for payload in discovered:
                base = payload.get("base", "").rstrip("/")
                if not base or base in seen:
                    continue
                deduped.append(payload)
                seen.add(base)
            self.signals.finished.emit(self.token, deduped)
            return

        self.signals.failed.emit(self.token, "; ".join(errors) or "no mirrors found")


class _SearchWorker(QRunnable):
    def __init__(self, token: int, source_id: str, source: Dict[str, str], query: str = ""):
        super().__init__()
        self.token = token
        self.source_id = source_id
        self.source = source
        self.query = query.lower().strip()
        self.signals = _SearchSignals()

    @staticmethod
    def _fetch_index(url: str) -> str:
        response = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=30,
        )
        response.raise_for_status()

        data = response.content
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _fetch_api(base: str, query: str) -> str:
        base = base.rstrip("/")
        api_candidates = [f"{base}/api/api.php", f"{base}/api.php"]
        params = {
            "out": "json",
            "query": query,
            "action": "search",
        }
        last_exc: Optional[Exception] = None

        for api_url in api_candidates:
            try:
                response = requests.get(
                    api_url,
                    headers={"User-Agent": UA},
                    params=params,
                    timeout=IDGAMES_API_TIMEOUT,
                )
                response.raise_for_status()
                return response.content.decode("utf-8", errors="ignore")
            except Exception as exc:  # pragma: no cover - network path
                last_exc = exc

        if query == "":
            # Keep the method deterministic; callers can fall back to local fullsort index.
            raise RuntimeError("idgames_api query missing")
        if last_exc:
            raise last_exc
        raise RuntimeError("idgames_api request failed")

    @staticmethod
    def _coerce_int(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        try:
            text = str(value).strip().replace(",", "").replace(" ", "")
            return int(float(text))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _normalize_api_path(value: str) -> str:
        if not value:
            return ""
        path = str(value).strip().replace("\\", "/").split("?")[0].split("#")[0]
        return _normalize_remote_path(path)

    @staticmethod
    def _collect_api_items(payload: Any) -> List[Dict[str, Any]]:
        if not payload:
            return []

        candidates: List[Dict[str, Any]] = []
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
                    candidates.extend(_SearchWorker._collect_api_items(payload[key]))
            return candidates

        if isinstance(payload, list):
            for entry in payload:
                candidates.extend(_SearchWorker._collect_api_items(entry))
            return candidates

        return []

    @staticmethod
    def _parse_idgames_api(
        source: Dict[str, str], text: str, query: str, allow_empty_query: bool = False
    ) -> List[WadBrowserResult]:
        query = query.lower().strip()
        if not query and not allow_empty_query:
            return []

        try:
            payload = json.loads(text)
        except Exception:
            return []

        entries = _SearchWorker._collect_api_items(payload)
        parsed: List[WadBrowserResult] = []
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

            if not _looks_like_mod_file(filename):
                continue

            path = _SearchWorker._normalize_api_path(
                str(item.get("path") or item.get("dir") or item.get("directory") or "")
            )
            remote_path = _normalize_remote_path(f"{path}/{filename}") if path else _normalize_remote_path(filename)
            if not remote_path:
                continue
            if remote_path in seen:
                continue

            haystack = f"{filename} {path} {str(item.get('title', ''))} {str(item.get('description', ''))} {str(item.get('author', ''))}".lower()
            if query and query not in haystack:
                continue

            size = 0
            for size_key in ("size", "size_bytes", "filesize", "length"):
                if item.get(size_key) not in (None, ""):
                    size = _SearchWorker._coerce_int(item[size_key])
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

            if not _looks_like_mod_file(_SearchWorker._normalize_api_path(download_url)):
                if remote_path:
                    download_url = urljoin(f"{source['base'].rstrip('/')}/", remote_path)

            parsed.append(
                WadBrowserResult(
                    title=filename,
                    description=description,
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
        fallback_paths: List[str] = []
        for key in ("path", "base", "file", "result", "download", "url"):
            if isinstance(payload, dict) and key in payload:
                fallback_paths.append(str(payload[key]))
        for entry in fallback_paths:
            normalized = _SearchWorker._normalize_api_path(entry)
            if not normalized or not _looks_like_mod_file(normalized):
                continue
            parsed.append(
                WadBrowserResult(
                    title=Path(normalized).name,
                    description="",
                    source_id=source.get("id", ""),
                    source_name=source.get("name", source.get("base", "")),
                    size_bytes=0,
                    remote_path=normalized,
                    download_url=urljoin(f"{source['base'].rstrip('/')}/", normalized),
                    browser_url=urljoin(f"{source['browser'].rstrip('/')}/", normalized),
                )
            )
        return parsed

    @staticmethod
    def _parse_json(source: Dict[str, str], text: str, query: str) -> List[WadBrowserResult]:
        return _SearchWorker._parse_idgames_api(source, text, query, allow_empty_query=True)

    @staticmethod
    def _parse_rss(source: Dict[str, str], text: str, query: str) -> List[WadBrowserResult]:
        query = query.lower().strip()
        if not text:
            return []

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        namespace = "{http://www.w3.org/2005/Atom}"
        items: List[ET.Element] = []
        if root.tag.startswith(namespace):
            items.extend(root.findall(f"{namespace}entry"))
        items.extend(root.findall(".//item"))
        if not items and root.tag.endswith("entry"):
            items.append(root)

        parsed: List[WadBrowserResult] = []
        seen = set()

        def _pick_text(node: ET.Element, keys: List[str]) -> str:
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
            link = _pick_text(item, [f"{namespace}link", "link", "link/@href"])
            if not link:
                link = _pick_attr(item, "link", "href")

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

            remote = _SearchWorker._normalize_api_path(urlparse(download_url).path if download_url else "")
            if not remote:
                remote = _SearchWorker._normalize_api_path(_pick_text(item, ["enclosure" ]))
            if not remote:
                remote = _SearchWorker._normalize_api_path(title)

            if not remote or not _looks_like_mod_file(remote):
                continue

            haystack = f"{title} {remote} {description}".lower()
            if query and query not in haystack:
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
                    size = _SearchWorker._coerce_int(size_attr.attrib.get(size_key, 0))
                    break

            parsed.append(
                WadBrowserResult(
                    title=title or Path(remote).name,
                    description=description,
                    source_id=source.get("id", ""),
                    source_name=source.get("name", source.get("base", "")),
                    size_bytes=size,
                    remote_path=remote,
                    download_url=download_url,
                    browser_url=browser_url,
                )
            )

        return parsed

    @staticmethod
    def _iter_html_links(html: str) -> Iterable[tuple[str, str]]:
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

    @staticmethod
    def _link_targets(html: str) -> Iterable[str]:
        for href, _ in _SearchWorker._iter_html_links(html):
            yield href

    @staticmethod
    def _relative_path_for(base_url: str, candidate_url: str) -> str:
        base_parts = urlparse(base_url)
        candidate = urlparse(candidate_url)
        if candidate.scheme != base_parts.scheme or candidate.netloc != base_parts.netloc:
            return ""
        base_path = base_parts.path.rstrip("/")
        raw = candidate.path
        if raw.endswith("/"):
            raw = raw[:-1]
        raw = raw.lstrip("/")
        if base_path and raw.startswith(base_path.rstrip("/") + "/"):
            raw = raw[len(base_path.rstrip("/") + "/"):]
        elif base_path and raw == base_path.rstrip("/"):
            raw = ""
        return raw

    @staticmethod
    def _parse_html(
        source: Dict[str, str], text: str, query: str
    ) -> List[tuple[int, str, str]]:
        query = query.lower().strip()
        found = []
        seen = set()
        base = source["base"].rstrip("/")
        base_path = urlparse(base).path.rstrip("/")
        for href, label in _SearchWorker._iter_html_links(text):
            if href in ("../", "./"):
                continue
            absolute = urljoin(base + "/", href)
            if not absolute.startswith(base):
                continue
            rel = _SearchWorker._relative_path_for(base, absolute)
            if not rel:
                continue
            rel = rel.lstrip("/")
            rel = _normalize_remote_path(rel)
            if rel in seen:
                continue
            if rel.endswith("/"):
                continue
            if rel.lower().endswith("/"):
                continue
            if not _looks_like_mod_file(rel):
                continue
            if query:
                search_blob = f"{rel} {label}".lower().replace("_", " ")
                if query not in search_blob:
                    continue
            seen.add(rel)
            found.append((0, rel, label[:140]))
            if len(found) >= HTML_CRAWL_MAX_FILES:
                break
        return found

    @staticmethod
    def _crawl_html(
        source: Dict[str, str],
        start_path: str,
        query: str,
    ) -> List[tuple[int, str, str]]:
        query = query.lower().strip()
        base = source["base"].rstrip("/")
        seen_dirs = set()
        discovered = set()
        results: List[tuple[int, str, str]] = []
        page = ""

        queue = deque([(start_path, 0)])
        while queue and len(seen_dirs) < HTML_CRAWL_MAX_DIRS and len(results) < HTML_CRAWL_MAX_FILES:
            rel_dir, depth = queue.popleft()
            rel_dir = rel_dir.strip().lstrip("/")
            current = f"{base}/"
            if rel_dir:
                current += f"{rel_dir.rstrip('/')}/"
            try:
                page = _SearchWorker._fetch_index(current)
            except Exception:
                continue

            if rel_dir not in seen_dirs:
                seen_dirs.add(rel_dir)

            for href, label in _SearchWorker._iter_html_links(page):
                if not href or href.startswith("#"):
                    continue
                if href.lower().startswith(("javascript:", "mailto:")):
                    continue
                absolute = urljoin(current, href)
                rel = _SearchWorker._relative_path_for(base, absolute).strip().lstrip("/")
                if not rel:
                    continue
                if rel in discovered:
                    continue

                if href.endswith("/") or absolute.endswith("/"):
                    if depth + 1 < HTML_CRAWL_MAX_DEPTH:
                        discovered.add(rel)
                        queue.append((rel, depth + 1))
                    continue

                if not _looks_like_mod_file(rel):
                    continue
                if query and query not in rel.lower():
                    if query not in label.lower():
                        continue
                    continue
                discovered.add(rel)
                results.append((0, rel, label[:140]))
                if len(results) >= HTML_CRAWL_MAX_FILES:
                    break

        if not query and not results and page:
            for parsed in _SearchWorker._parse_html(source, page, query):
                if parsed:
                    results.append(parsed)
        return results

    @staticmethod
    def _crawl_start_path(start: str) -> str:
        crawl_start = str(start or "").strip()
        if crawl_start.lower().endswith((".gz", ".zip", ".txt", ".tgz")):
            return ""
        return crawl_start

    @staticmethod
    def _looks_like_datetime_token(raw: str) -> bool:
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

    @staticmethod
    def _parse_fullsort_tokens(tokens: List[str]) -> Optional[tuple[int, str, str]]:
        if not tokens:
            return None

        # Common formats observed across idgames mirrors vary:
        #  - date size path desc...
        #  - size path desc...
        #  - date time size path desc...
        idx = 0
        if _SearchWorker._looks_like_datetime_token(tokens[idx]):
            idx += 1
            if idx < len(tokens) and _SearchWorker._looks_like_datetime_token(tokens[idx]):
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

        remote_path = _normalize_remote_path(tokens[path_index])
        description = " ".join(tokens[path_index + 1 :]).strip()
        return size, remote_path, description

    @staticmethod
    def _parse_fullsort(text: str) -> List[tuple[int, str, str]]:
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("fullpath") or line.startswith("Control switch"):
                continue
            if line.startswith("#") or line.startswith(";"):
                continue

            parsed = _SearchWorker._parse_fullsort_tokens(line.split())
            if not parsed:
                continue
            size, path, description = parsed
            if not path:
                continue
            if not _looks_like_mod_file(path):
                continue
            entries.append((size, path, description))
        return entries

    @staticmethod
    def _parse_text(text: str, source: Dict[str, str], query: str) -> List[tuple[int, str, str]]:
        query = query.lower().strip()
        base = source["base"].rstrip("/")
        browser = source["browser"].rstrip("/")
        if not base:
            return []

        entries: List[tuple[int, str, str]] = []
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
            if _looks_like_mod_file(_normalize_remote_path(parts[0])):
                candidate = parts[0]
                tail = " ".join(parts[1:])
            elif len(parts) > 1 and _looks_like_mod_file(_normalize_remote_path(parts[1])):
                parsed_size = _SearchWorker._coerce_int(parts[0])
                if parsed_size:
                    size = parsed_size
                candidate = parts[1]
                tail = " ".join(parts[2:])
            else:
                remainder = None
                for part in parts:
                    normalized = _normalize_remote_path(part)
                    if _looks_like_mod_file(normalized):
                        candidate = part
                        remainder = raw
                        break
                if remainder is None:
                    continue
                tail = remainder

            remote = _normalize_remote_path(candidate)
            if not remote or not _looks_like_mod_file(remote):
                continue

            haystack = f"{raw} {tail} {candidate}".lower()
            if query and query not in haystack:
                continue

            if remote.startswith(base):
                download_url = remote
                browser_url = remote
            else:
                download_url = urljoin(f"{base}/", remote)
                browser_url = urljoin(f"{browser}/", remote)

            entries.append((size, remote, tail.strip()))

        return entries

    def run(self):
        try:
            parser = _coerce_parser(self.source.get("parser", DEFAULT_SOURCE_PARSER))
            start = str(self.source.get("index", "")).strip()
            if not start:
                start = "fullsort.gz"
            base = self.source["base"].rstrip("/")
            query = self.query

            if parser == "auto":
                raw_items = []
                if query:
                    try:
                        raw_items = self._parse_idgames_api(self.source, self._fetch_api(base, query), query)
                    except Exception:
                        raw_items = []

                if not raw_items:
                    try:
                        text = self._fetch_index(f"{base}/{start}")
                        raw_items = self._parse_fullsort(text)
                        if not raw_items:
                            raw_items = self._parse_json(self.source, text, query)
                        if not raw_items:
                            raw_items = self._parse_rss(self.source, text, query)
                    except Exception:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, query)
            elif parser == "idgames_api":
                if query:
                    try:
                        raw_items = self._parse_idgames_api(self.source, self._fetch_api(base, query), query)
                        if not raw_items:
                            raise RuntimeError("idgames_api returned no results")
                    except Exception:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, query)
                else:
                    text = self._fetch_index(f"{base}/{start}")
                    raw_items = self._parse_fullsort(text)
                    if not raw_items:
                        raw_items = self._parse_json(self.source, text, query)
                    if not raw_items:
                        raw_items = self._parse_rss(self.source, text, query)
                    if not raw_items:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, query)
            elif parser == "json":
                text = self._fetch_index(f"{base}/{start}")
                raw_items = self._parse_json(self.source, text, query)
                if not raw_items and query:
                    try:
                        raw_items = self._parse_idgames_api(self.source, self._fetch_api(base, query), query)
                    except Exception:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, query)
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, query)
            elif parser == "html":
                crawl_start = self._crawl_start_path(start)
                raw_items = self._crawl_html(self.source, crawl_start, query)
            elif parser == "rss":
                text = self._fetch_index(f"{base}/{start}")
                raw_items = self._parse_rss(self.source, text, query)
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, query)
            elif parser == "text":
                index_url = urljoin(base + "/", start)
                text = self._fetch_index(index_url)
                raw_items = self._parse_text(text, self.source, query)
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, query)
            else:
                index_url = urljoin(base + "/", start)
                try:
                    text = self._fetch_index(index_url)
                    raw_items = self._parse_fullsort(text)
                    if not raw_items:
                        raw_items = self._parse_json(self.source, text, query)
                    if not raw_items:
                        raw_items = self._parse_rss(self.source, text, query)
                except Exception:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, query)

            parsed: List[WadBrowserResult] = []
            for item in raw_items:
                if parser in {"idgames_api", "json", "rss"} and isinstance(item, WadBrowserResult):
                    parsed.append(item)
                    continue

                size = int(item[0])
                path = item[1]
                description = item[2] if len(item) > 2 else ""
                parsed.append(
                    WadBrowserResult(
                        title=Path(path).name,
                        description=description,
                        source_id=self.source_id,
                        source_name=self.source["name"],
                        size_bytes=size,
                        remote_path=path,
                        download_url=urljoin(self.source["base"].rstrip("/") + "/", path),
                        browser_url=urljoin(self.source["browser"].rstrip("/") + "/", path),
                    )
                )
            self.signals.finished.emit(self.token, self.source_id, parsed)
        except Exception as exc:  # pragma: no cover - network path
            self.signals.failed.emit(self.token, self.source_id, str(exc))


class _DownloadWorker(QRunnable):
    def __init__(self, token: int, url: str, destination: str):
        super().__init__()
        self.token = token
        self.url = url
        self.destination = destination
        self.signals = _DownloadSignals()

    @pyqtSlot()
    def run(self):
        try:
            with requests.get(
                self.url,
                stream=True,
                headers={"User-Agent": UA},
                timeout=120,
            ) as response:
                response.raise_for_status()

                total = int(response.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                tmp = f"{self.destination}.part"
                with open(tmp, "wb") as fp:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        fp.write(chunk)
                        downloaded += len(chunk)
                        self.signals.progress.emit(self.token, downloaded, total, self.url)
                os.replace(tmp, self.destination)
            self.signals.finished.emit(self.token, self.destination, self.url)
        except Exception as exc:  # pragma: no cover - network path
            tmp = f"{self.destination}.part"
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.signals.failed.emit(self.token, self.url, str(exc))


def _looks_like_mod_file(path: str) -> bool:
    path = path.lower()
    return path.endswith(DEFAULT_EXTENSIONS)


def _normalize_remote_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    if not path:
        return ""
    try:
        path = unquote(path)
    except Exception:
        pass
    return path.lstrip("/")


def _result_identity(result: WadBrowserResult) -> str:
    remote_path = _normalize_remote_path(result.remote_path).lower()
    if remote_path:
        return f"remote:{remote_path}"
    remote_url = _normalize_remote_path(urlparse(result.download_url).path).lower()
    if remote_url:
        return f"url:{remote_url}"
    return f"name:{result.title.lower()}"


def _normalize_source_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")


def _safe_library_name(path: str, source_id: str, target_dir: Path) -> Path:
    file_name = os.path.basename(path)
    if not file_name:
        file_name = f"mod_{source_id}.pkg"
    candidate = target_dir / file_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    idx = 1
    while True:
        alt = target_dir / f"{stem}_{idx}__{source_id}{suffix}"
        if not alt.exists():
            return alt
        idx += 1


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "—"
    units = [(1024**4, "TB"), (1024**3, "GB"), (1024**2, "MB"), (1024, "KB")]
    for divisor, name in units:
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.1f} {name}"
    return f"{size_bytes} B"


class _SourcePriorityList(QListWidget):
    orderChanged = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()


def _short_age(seconds: float) -> str:
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


class WadFinder(QWidget):
    addRequested = pyqtSignal(list)
    removedRequested = pyqtSignal(list)
    statusChanged = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        library_dir: Optional[str] = None,
        source_state: Optional[List[Dict[str, Any]]] = None,
        source_state_changed=None,
        library_dir_changed=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Mod Browser")
        self.setToolTip("Search and download community PWADs and add them to launch order")

        self.searchSources: Dict[str, Dict[str, str]] = {}
        self.sourceOrder: List[str] = []
        self._seed_sources(source_state)
        self.source_state_changed = source_state_changed
        self.library_dir_changed = library_dir_changed

        cache_root = Path(os.getenv("BFG_CACHE_DIR", Path.home() / ".cache" / "bfg.py"))
        self.cache_root = cache_root
        self.library_dir = Path(library_dir).expanduser() if library_dir else cache_root / "mods"
        self.library_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_root / "source_index").mkdir(parents=True, exist_ok=True)
        self._health_file = self.cache_root / "source_health.json"

        self._thread_pool = QThreadPool.globalInstance()
        self._source_health: Dict[str, Dict[str, Any]] = {}
        self._search_token = 0
        self._discover_token = 0
        self._download_token = 0
        self._search_sessions: Dict[int, Dict[str, Any]] = {}
        self._discover_session: Optional[int] = None
        self._download_sessions: Dict[int, Dict[str, Any]] = {}
        self._index_cache: Dict[str, _IndexCacheEntry] = {}
        self._rendered_results: List[WadBrowserResult] = []
        self._failed_downloads: List[WadBrowserResult] = []
        self._failed_download_auto_add: bool = False
        self._library_rows: List[Dict[str, Any]] = []

        self._load_index_cache()
        self._load_source_health_cache()
        self.initUi()
        self._emit_source_state()

    @staticmethod
    def _coerce_source(raw: Dict[str, Any]) -> Dict[str, str]:
        status = str(raw.get("status", SOURCE_STATUS_UNKNOWN)).lower().strip()
        if status not in {
            SOURCE_STATUS_UNKNOWN,
            SOURCE_STATUS_CHECKING,
            SOURCE_STATUS_CACHED,
            SOURCE_STATUS_OK,
            SOURCE_STATUS_UNREACHABLE,
            SOURCE_STATUS_ERROR,
            SOURCE_STATUS_DISABLED,
        }:
            status = SOURCE_STATUS_UNKNOWN

        source_id = _normalize_source_id(
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
            "id": _normalize_source_id(source_id),
            "name": name,
            "base": base.rstrip("/"),
            "index": index,
            "browser": browser.rstrip("/"),
            "parser": _coerce_parser(raw.get("parser", DEFAULT_SOURCE_PARSER)),
            "enabled": bool(raw.get("enabled", True)),
            "status": status,
            "status_message": str(raw.get("status_message", "")).strip(),
            "status_checked_at": float(raw.get("status_checked_at", 0.0) or 0.0),
        }

    def _seed_sources(self, source_state: Optional[List[Dict[str, Any]]]):
        defaults = [self._coerce_source(item) for item in DEFAULT_SOURCES]
        if source_state:
            defaults = []
            for item in source_state:
                if not isinstance(item, dict):
                    continue
                defaults.append(self._coerce_source(item))
            if not defaults:
                defaults = [self._coerce_source(item) for item in DEFAULT_SOURCES]
            else:
                existing = {_normalize_source_id(str(item.get("id", ""))) for item in defaults}
                for item in DEFAULT_SOURCES:
                    source_id = _normalize_source_id(item["id"])
                    if source_id not in existing:
                        defaults.append(self._coerce_source(item))

        self.searchSources = {}
        self.sourceOrder = []
        seen = set()
        for entry in defaults:
            source_id = entry["id"]
            if not source_id or source_id in seen:
                continue
            if not entry["base"]:
                continue
            self.searchSources[source_id] = entry
            self.sourceOrder.append(source_id)
            seen.add(source_id)

    def initUi(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        title = QLabel("PWAD Browser")
        title.setFrameStyle(QFrame.Box | QFrame.Raised)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # Search controls.
        searchRow = QHBoxLayout()
        self.sourceCombo = QComboBox()
        self.sourceCombo.setMinimumWidth(220)
        self.sourceCombo.currentIndexChanged.connect(self._on_search_source_changed)
        self._refresh_source_combo()

        self.searchInput = QLineEdit()
        self.searchInput.setPlaceholderText("Search by filename, map, author, or mod name")
        self.searchInput.returnPressed.connect(self._on_search)

        self.searchButton = QPushButton("Search")
        self.searchButton.clicked.connect(self._on_search)
        self.clearSearchButton = QPushButton("Clear")
        self.clearSearchButton.clicked.connect(self._on_clear_search)
        searchRow.addWidget(self.sourceCombo, 1)
        searchRow.addWidget(self.searchInput, 3)
        searchRow.addWidget(self.searchButton)
        searchRow.addWidget(self.clearSearchButton)
        root.addLayout(searchRow)

        # Source management for priority and optional disable.
        sourceHeader = QLabel("Sources & priority")
        sourceHeader.setStyleSheet("font-weight: 600;")
        root.addWidget(sourceHeader)

        self.sourceOrderList = _SourcePriorityList()
        self.sourceOrderList.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sourceOrderList.itemSelectionChanged.connect(self._on_selection_changed)
        self.sourceOrderList.orderChanged.connect(self._sync_source_order_from_ui)
        self.sourceOrderList.setDragDropMode(QAbstractItemView.InternalMove)
        self.sourceOrderList.setDragEnabled(True)
        self.sourceOrderList.setAcceptDrops(True)
        self.sourceOrderList.setDropIndicatorShown(True)
        self.sourceOrderList.setDefaultDropAction(Qt.MoveAction)
        self._rebuild_source_list()

        sourceButtons = QHBoxLayout()
        self.sourceUpButton = QPushButton("Move Up")
        self.sourceUpButton.clicked.connect(lambda: self._reorder_source(-1))
        self.sourceDownButton = QPushButton("Move Down")
        self.sourceDownButton.clicked.connect(lambda: self._reorder_source(1))
        self.sourceTopButton = QPushButton("Move to Top")
        self.sourceTopButton.clicked.connect(self._move_source_to_top)
        self.sourceBottomButton = QPushButton("Move to Bottom")
        self.sourceBottomButton.clicked.connect(self._move_source_to_bottom)
        self.sourceRemoveButton = QPushButton("Remove")
        self.sourceRemoveButton.clicked.connect(self._remove_selected_custom_sources)
        self.sourceToggleButton = QPushButton("Enable/Disable")
        self.sourceToggleButton.clicked.connect(self._toggle_selected_source)
        self.sourceEnableAllButton = QPushButton("Enable All")
        self.sourceEnableAllButton.clicked.connect(lambda: self._set_all_sources_enabled(True))
        self.sourceDisableAllButton = QPushButton("Disable All")
        self.sourceDisableAllButton.clicked.connect(lambda: self._set_all_sources_enabled(False))
        self.sourcePurgeButton = QPushButton("Purge Unavailable")
        self.sourcePurgeButton.setToolTip("Remove user/discovered sources that are unavailable")
        self.sourcePurgeButton.clicked.connect(self._purge_unavailable_sources)

        sourceButtons.addWidget(self.sourceUpButton)
        sourceButtons.addWidget(self.sourceDownButton)
        sourceButtons.addWidget(self.sourceTopButton)
        sourceButtons.addWidget(self.sourceBottomButton)
        sourceButtons.addWidget(self.sourceToggleButton)
        sourceButtons.addWidget(self.sourceEnableAllButton)
        sourceButtons.addWidget(self.sourceDisableAllButton)
        sourceButtons.addWidget(self.sourcePurgeButton)
        sourceButtons.addWidget(self.sourceRemoveButton)

        discoverRow = QHBoxLayout()
        self.discoverSeedInput = QLineEdit()
        self.discoverSeedInput.setPlaceholderText(
            "Optional discovery seed URLs (comma/space separated)"
        )
        self.discoverSeedInput.setToolTip("Leave blank to use built-in discovery seeds.")
        self.discoverSourcesButton = QPushButton("Discover Doomworld Mirrors")
        self.discoverSourcesButton.setToolTip("Fetch current Doomworld/idgames mirrors and add any missing")
        self.discoverSourcesButton.clicked.connect(self._discover_mirrors)
        self.discoverClearDiscoveredButton = QPushButton("Clear Discovered")
        self.discoverClearDiscoveredButton.clicked.connect(self._clear_discovered_sources)
        discoverRow.addWidget(self.discoverSeedInput, 1)
        discoverRow.addWidget(self.discoverSourcesButton)
        discoverRow.addWidget(self.discoverClearDiscoveredButton)

        sourceAddRow = QHBoxLayout()
        self.customSourceInput = QLineEdit()
        self.customSourceInput.setPlaceholderText(
            "Add source(s): URL | Name | index | parser. One per line."
        )
        self.customSourceInput.setMinimumHeight(26)
        self.customSourceButton = QPushButton("Add Source")
        self.customSourceButton.clicked.connect(self._add_custom_source)
        self.customSourceListReset = QPushButton("Clear Custom")
        self.customSourceListReset.clicked.connect(self._clear_custom_source)
        self.customSourcePresetButton = QPushButton("Add Built-in Sources")
        self.customSourcePresetButton.clicked.connect(self._add_builtin_sources)

        sourceAddRow.addWidget(self.customSourceInput, 2)
        sourceAddRow.addWidget(self.customSourceButton)
        sourceAddRow.addWidget(self.customSourceListReset)
        sourceAddRow.addWidget(self.customSourcePresetButton)

        localFilterRow = QHBoxLayout()
        localFilterLabel = QLabel("Library filter:")
        self.localFilterInput = QLineEdit()
        self.localFilterInput.setPlaceholderText("Filter library by name, source, or remote path")
        self.localFilterInput.textChanged.connect(self._refresh_local_library)
        localFilterRow.addWidget(localFilterLabel)
        localFilterRow.addWidget(self.localFilterInput, 1)

        root.addWidget(self.sourceOrderList)
        root.addLayout(sourceButtons)
        root.addLayout(discoverRow)
        root.addLayout(sourceAddRow)
        root.addLayout(localFilterRow)

        # Search + local library.
        self.resultsTree = QTreeWidget()
        self.resultsTree.setHeaderLabels(["Source", "Mod File", "Size", "Path"])
        self.resultsTree.setSelectionMode(self.resultsTree.ExtendedSelection)
        self.resultsTree.setSortingEnabled(True)
        self.resultsTree.itemSelectionChanged.connect(self._on_selection_changed)
        self.resultsTree.itemDoubleClicked.connect(self._add_selected)
        self.resultsTree.setMinimumHeight(150)

        self.localTree = QTreeWidget()
        self.localTree.setHeaderLabels(["Local Mod", "Size", "Source", "Source Path"])
        self.localTree.setSelectionMode(self.localTree.ExtendedSelection)
        self.localTree.setSortingEnabled(True)
        self.localTree.itemSelectionChanged.connect(self._on_selection_changed)
        self.localTree.itemDoubleClicked.connect(self._add_selected)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.resultsTree)
        splitter.addWidget(self.localTree)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 2)

        actions = QHBoxLayout()
        self.openButton = QPushButton("Open in Browser")
        self.openButton.clicked.connect(self._open_selected_remote)

        self.downloadButton = QPushButton("Download")
        self.downloadButton.clicked.connect(self._download_selected)

        self.selectAllResultsButton = QPushButton("Select All Results")
        self.selectAllResultsButton.clicked.connect(self._select_all_results)

        self.downloadAllButton = QPushButton("Download All Results")
        self.downloadAllButton.clicked.connect(self._download_all_results)
        self.retryFailedButton = QPushButton("Retry Failed")
        self.retryFailedButton.clicked.connect(self._retry_failed_downloads)
        self.retryFailedButton.setToolTip("Retry only the failed downloads from the last batch.")

        self.addAllButton = QPushButton("Add All Results")
        self.addAllButton.clicked.connect(self._add_all_results)

        self.clearResultsSelectionButton = QPushButton("Clear Result Selection")
        self.clearResultsSelectionButton.clicked.connect(self._clear_results_selection)

        self.addButton = QPushButton("Add to Launch List")
        self.addButton.clicked.connect(self._add_selected)

        self.deleteButton = QPushButton("Delete Downloaded")
        self.deleteButton.clicked.connect(self._delete_selected_local)
        self.selectAllLocalButton = QPushButton("Select All Local")
        self.selectAllLocalButton.clicked.connect(self._select_all_local)
        self.clearLocalSelectionButton = QPushButton("Clear Local Selection")
        self.clearLocalSelectionButton.clicked.connect(self._clear_local_selection)
        self.openLocalButton = QPushButton("Open File")
        self.openLocalButton.clicked.connect(self._open_selected_local_file)
        self.openLocalFolderButton = QPushButton("Open Folder")
        self.openLocalFolderButton.clicked.connect(self._open_selected_local_folder)

        self.openLibraryButton = QPushButton("Open Library Folder")
        self.openLibraryButton.clicked.connect(self._open_library_dir)
        self.libraryDirButton = QPushButton("Set Library Folder")
        self.libraryDirButton.clicked.connect(self._change_library_dir)

        actions.addWidget(self.openButton)
        actions.addWidget(self.downloadButton)
        actions.addWidget(self.downloadAllButton)
        actions.addWidget(self.retryFailedButton)
        actions.addWidget(self.selectAllResultsButton)
        actions.addWidget(self.clearResultsSelectionButton)
        actions.addWidget(self.addButton)
        actions.addWidget(self.addAllButton)
        actions.addWidget(self.deleteButton)
        actions.addWidget(self.selectAllLocalButton)
        actions.addWidget(self.clearLocalSelectionButton)
        actions.addWidget(self.openLocalButton)
        actions.addWidget(self.openLocalFolderButton)
        actions.addWidget(self.openLibraryButton)
        actions.addWidget(self.libraryDirButton)
        root.addLayout(actions)

        self.libraryDirLabel = QLabel(f"Library folder: {self.library_dir}")
        root.addWidget(self.libraryDirLabel)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setWordWrap(True)
        root.addWidget(self.statusLabel)

        self._refresh_local_library()
        self._refresh_retry_state()
        self._set_controls_enabled(True)
        self._on_selection_changed()

    def _load_index_cache(self):
        cache_dir = self.cache_root / "source_index"
        for source_id, source in self.searchSources.items():
            cache_file = cache_dir / f"{source_id}.json"
            if not cache_file.exists():
                continue
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                entries = payload.get("entries", [])
                parsed: List[WadBrowserResult] = []
                for item in entries:
                    parsed.append(
                        WadBrowserResult(
                            title=item["title"],
                            description=item.get("description", ""),
                            source_id=item["source_id"],
                            source_name=item["source_name"],
                            size_bytes=int(item["size_bytes"]),
                            remote_path=item["remote_path"],
                            download_url=item["download_url"],
                            browser_url=item["browser_url"],
                        )
                    )
                fetched_at = float(payload.get("fetched_at", 0.0))
                if time.time() - fetched_at <= INDEX_TTL_SECONDS:
                    self._index_cache[source_id] = _IndexCacheEntry(
                        source_id=source_id,
                        source_name=source["name"],
                        fetched_at=fetched_at,
                        entries=parsed,
                    )
                    source["status"] = SOURCE_STATUS_CACHED
                    source["status_message"] = f"Cached index: {_short_age(time.time() - fetched_at)}"
                    source["status_checked_at"] = fetched_at
                else:
                    try:
                        cache_file.unlink()
                    except OSError:
                        pass
            except Exception:
                continue

    def _load_source_health_cache(self):
        if not self._health_file.exists():
            return
        try:
            payload = json.loads(self._health_file.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return
        for source_id, value in payload.items():
            if not isinstance(value, dict):
                continue
            try:
                self._source_health[source_id] = {
                    "status": str(value.get("status", SOURCE_STATUS_UNKNOWN)).strip().lower(),
                    "status_message": str(value.get("status_message", "")).strip(),
                    "status_checked_at": float(value.get("status_checked_at", 0.0) or 0.0),
                }
            except Exception:
                continue

        for source_id, health in self._source_health.items():
            source = self.searchSources.get(source_id)
            if not source:
                continue
            source["status"] = health.get("status", SOURCE_STATUS_UNKNOWN)
            source["status_message"] = str(health.get("status_message", ""))
            source["status_checked_at"] = float(health.get("status_checked_at", 0.0) or 0.0)

    def _store_index_cache(self, source_id: str, source: Dict[str, str], entries: List[WadBrowserResult]):
        cache_file = self.cache_root / "source_index" / f"{source_id}.json"
        trimmed = entries[:MAX_CACHED_INDEX_ENTRIES]
        payload = {
            "source_id": source_id,
            "source_name": source["name"],
            "fetched_at": time.time(),
            "entries": [
                {
                    "title": item.title,
                    "description": item.description,
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "size_bytes": item.size_bytes,
                    "remote_path": item.remote_path,
                    "download_url": item.download_url,
                    "browser_url": item.browser_url,
                }
                for item in trimmed
            ],
        }
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _store_source_health_cache(self):
        try:
            self._health_file.write_text(
                json.dumps(self._source_health, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _set_source_status(
        self,
        source_id: str,
        status: str,
        message: str = "",
        *,
        persist: bool = False,
    ):
        source = self.searchSources.get(source_id)
        if not source:
            return

        if status not in {
            SOURCE_STATUS_UNKNOWN,
            SOURCE_STATUS_CHECKING,
            SOURCE_STATUS_CACHED,
            SOURCE_STATUS_OK,
            SOURCE_STATUS_UNREACHABLE,
            SOURCE_STATUS_ERROR,
            SOURCE_STATUS_DISABLED,
        }:
            status = SOURCE_STATUS_UNKNOWN

        source["status"] = status
        source["status_message"] = str(message or "").strip()
        source["status_checked_at"] = time.time()

        if status in {SOURCE_STATUS_DISABLED}:
            source["enabled"] = False

        self._source_health[source_id] = {
            "status": source["status"],
            "status_message": source["status_message"],
            "status_checked_at": source["status_checked_at"],
        }
        if persist:
            self._store_source_health_cache()

        self._rebuild_source_list()
        self._emit_source_state()

    def _is_cache_valid(self, source_id: str) -> bool:
        entry = self._index_cache.get(source_id)
        if not entry:
            return False
        return (time.time() - entry.fetched_at) <= INDEX_TTL_SECONDS

    @staticmethod
    def _source_tooltip(source: Dict[str, str]) -> str:
        base = source.get("browser", source.get("base", ""))
        try:
            checked_at = float(source.get("status_checked_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            checked_at = 0.0
        if checked_at:
            age = _short_age(time.time() - checked_at)
            status_message = source.get("status_message", "").strip()
            if status_message:
                return f"{base}\nStatus: {source.get('status', 'unknown')} ({status_message})\nLast checked: {age}"
            return f"{base}\nStatus: {source.get('status', 'unknown')}\nLast checked: {age}"
        return base

    def _source_label(self, source: Dict[str, str], rank: int = 0) -> str:
        status = source.get("status", SOURCE_STATUS_UNKNOWN)
        status_text = {
            SOURCE_STATUS_OK: "ok",
            SOURCE_STATUS_CACHED: "cached",
            SOURCE_STATUS_CHECKING: "checking",
            SOURCE_STATUS_UNREACHABLE: "offline",
            SOURCE_STATUS_ERROR: "error",
            SOURCE_STATUS_DISABLED: "disabled",
            SOURCE_STATUS_UNKNOWN: "unknown",
        }.get(status, "unknown")
        prefix = f"{rank}. " if rank > 0 else ""
        return f"{prefix}{source['name']} ({source['base']}) [{status_text}]"

    def _refresh_source_list(self):
        self._rebuild_source_list()
        self._refresh_source_combo()

    def _emit_source_state(self):
        if not callable(self.source_state_changed):
            return
        self.source_state_changed(
            [
                source.copy()
                for source_id in self.sourceOrder
                if (source := self.searchSources.get(source_id))
            ]
        )

    def _rebuild_source_list(self):
        checked_map = {}
        selected = self.sourceOrderList.currentItem().data(Qt.UserRole) if self.sourceOrderList.currentItem() else None
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            checked_map[source_id] = item.checkState() == Qt.Checked
        selected_idx = 0
        selected_found = -1

        self.sourceOrderList.blockSignals(True)
        self.sourceOrderList.clear()
        for source_id in list(self.sourceOrder):
            source = self.searchSources.get(source_id)
            if not source:
                continue
            rank = list_index = self.sourceOrder.index(source_id)
            item = QListWidgetItem(self._source_label(source, rank + 1))
            item.setData(Qt.UserRole, source_id)
            item.setToolTip(self._source_tooltip(source))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked
                if checked_map.get(source_id, source.get("enabled", True))
                else Qt.Unchecked
            )
            item.setCheckable(True)
            self.sourceOrderList.addItem(item)
            if selected == source_id and selected_found < 0:
                selected_found = selected_idx
            selected_idx += 1
        self.sourceOrderList.blockSignals(False)

        if selected_found >= 0:
            self.sourceOrderList.setCurrentRow(selected_found)

    def _refresh_source_combo(self):
        previous_data = self.sourceCombo.currentData()
        self.sourceCombo.blockSignals(True)
        self.sourceCombo.clear()
        self.sourceCombo.addItem("All active sources", "all")
        for source_id in self.sourceOrder:
            source = self.searchSources.get(source_id)
            if not source:
                continue
            self.sourceCombo.addItem(source["name"], source_id)
        self.sourceCombo.blockSignals(False)

        idx = self.sourceCombo.findData(previous_data)
        self.sourceCombo.setCurrentIndex(0 if idx < 0 else idx)

    def _sync_source_order_from_ui(self):
        selected = self.sourceOrderList.currentItem().data(Qt.UserRole) if self.sourceOrderList.currentItem() else None
        ordered = []
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            if source_id in self.searchSources:
                ordered.append(source_id)

        if ordered and ordered != self.sourceOrder:
            self.sourceOrder = ordered
            self._emit_source_state()
            self._refresh_source_combo()

            if selected in self.sourceOrder:
                self.sourceOrderList.blockSignals(True)
                self.sourceOrderList.setCurrentRow(self.sourceOrder.index(selected))
                self.sourceOrderList.blockSignals(False)

    def _on_search_source_changed(self, *_):
        self._on_search()

    def _set_controls_enabled(self, enabled: bool):
        self.searchButton.setEnabled(enabled)
        self.clearSearchButton.setEnabled(enabled)
        self.selectAllResultsButton.setEnabled(enabled)
        self.clearResultsSelectionButton.setEnabled(enabled)
        self.customSourceButton.setEnabled(enabled)
        self.customSourceInput.setEnabled(enabled)
        self.customSourceListReset.setEnabled(enabled)
        self.customSourcePresetButton.setEnabled(enabled)
        self.discoverSourcesButton.setEnabled(enabled)
        self.sourceUpButton.setEnabled(enabled)
        self.sourceDownButton.setEnabled(enabled)
        self.sourceTopButton.setEnabled(enabled)
        self.sourceBottomButton.setEnabled(enabled)
        self.sourceToggleButton.setEnabled(enabled)
        self.sourceEnableAllButton.setEnabled(enabled)
        self.sourceDisableAllButton.setEnabled(enabled)
        self.sourcePurgeButton.setEnabled(enabled)
        self.sourceRemoveButton.setEnabled(enabled)
        self.discoverSeedInput.setEnabled(enabled)
        self.sourceOrderList.setEnabled(enabled)
        self.searchInput.setEnabled(enabled)
        self.openButton.setEnabled(enabled)
        self.downloadButton.setEnabled(enabled)
        self.downloadAllButton.setEnabled(enabled and bool(self._rendered_results))
        self.addAllButton.setEnabled(enabled and bool(self._rendered_results))
        self.addButton.setEnabled(enabled)
        self.deleteButton.setEnabled(enabled)
        self.selectAllLocalButton.setEnabled(enabled and bool(self.localTree.topLevelItemCount()))
        self.clearLocalSelectionButton.setEnabled(enabled and bool(self.localTree.selectedItems()))
        self.discoverSourcesButton.setEnabled(enabled)
        self.discoverClearDiscoveredButton.setEnabled(enabled)
        self.customSourceButton.setEnabled(enabled)
        self.customSourceInput.setEnabled(enabled)
        self.customSourceListReset.setEnabled(enabled)
        self.customSourcePresetButton.setEnabled(enabled)
        self.openLocalButton.setEnabled(enabled and bool(self._selected_local_files()))
        self.openLocalFolderButton.setEnabled(enabled and bool(self._selected_local_files()))
        self.openLibraryButton.setEnabled(enabled)
        self.libraryDirButton.setEnabled(enabled)
        self.resultsTree.setEnabled(enabled)
        self.localTree.setEnabled(enabled)
        self.sourceCombo.setEnabled(enabled)
        self._refresh_retry_state()

    def _refresh_retry_state(self):
        self.retryFailedButton.setEnabled(
            not self._is_busy()
            and bool(self._failed_downloads)
        )

    def _update_failed_downloads(self, session: Dict[str, Any]):
        failed_results: List[WadBrowserResult] = list(session.get("failed_results", []))
        if not failed_results:
            self._failed_downloads = []
            self.retryFailedButton.setToolTip("No failed downloads to retry")
            return

        deduped: List[WadBrowserResult] = []
        seen = set()
        for result in failed_results:
            key = _result_identity(result)
            if not key:
                key = result.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)

        self._failed_downloads = deduped
        self._failed_download_auto_add = bool(session.get("auto_add", False))
        self.retryFailedButton.setToolTip(
            f"Retry {len(self._failed_downloads)} failed download(s)"
        )

    def _is_busy(self) -> bool:
        return bool(self._search_sessions or self._download_sessions or self._discover_session is not None)

    def _refresh_controls_for_state(self):
        self._set_controls_enabled(not self._is_busy())

    def _set_status(self, text: str):
        self.statusLabel.setText(text)
        self.statusChanged.emit(text)

    def _change_library_dir(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select PWAD library folder",
            str(self.library_dir),
        )
        if not selected:
            return
        self._set_library_dir(selected)

    def _set_library_dir(self, directory: str):
        new_dir = Path(directory).expanduser()
        if not new_dir.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir = new_dir
        self.libraryDirLabel.setText(f"Library folder: {self.library_dir}")
        if callable(self.library_dir_changed):
            self.library_dir_changed(str(self.library_dir))
        self._refresh_local_library()
        self._set_status(f"Library directory set to {self.library_dir}")

    def _selected_source_ids(self) -> List[str]:
        selected_source = self.sourceCombo.currentData()
        if selected_source is None:
            return []

        if selected_source != "all":
            source = self.searchSources.get(selected_source)
            if source and source.get("enabled", True):
                return [selected_source]
            return []

        ordered = []
        for idx in range(self.sourceOrderList.count()):
            item = self.sourceOrderList.item(idx)
            source_id = item.data(Qt.UserRole)
            source = self.searchSources.get(source_id)
            if not source:
                continue
            if (item.checkState() == Qt.Checked and source.get("enabled", True)) and source_id in self.searchSources:
                ordered.append(source_id)
        return ordered

    def _iter_source_rows(self, ids: Iterable[str]):
        for source_id in self.sourceOrder:
            if source_id in ids:
                yield source_id

    def _add_custom_source(self):
        text = self.customSourceInput.text().strip()
        if not text:
            self._set_status("Enter a mirror URL first.")
            return

        entries: List[dict] = []
        for raw in [line.strip() for line in text.splitlines() if line.strip()]:
            parsed = self._parse_custom_source_entry(raw)
            if parsed is None:
                continue
            entries.append(parsed)

        if not entries:
            self._set_status("No valid source entries found.")
            return

        added = 0
        skipped = 0
        for parsed in entries:
            if self._is_duplicate_source_base(parsed["url"]):
                skipped += 1
                continue

            source_id = self._allocate_source_id(
                self._custom_source_id(parsed["url"], parsed["name"], parsed.get("base"))
            )

            source_name = parsed["name"] or self._make_host_name(parsed["url"])
            source_url = parsed["url"]
            source_index = parsed["index"]
            source_parser = parsed["parser"]
            self.searchSources[source_id] = {
                "id": source_id,
                "name": source_name,
                "base": source_url.rstrip("/"),
                "index": source_index,
                "browser": source_url.rstrip("/"),
                "parser": source_parser,
                "enabled": True,
                "status": SOURCE_STATUS_UNKNOWN,
                "status_message": "Custom source pending reachability check",
                "status_checked_at": 0.0,
            }
            self.sourceOrder.append(source_id)
            added += 1

        if added == 0 and skipped:
            self._set_status("No new sources added. They were duplicates.")
        elif added:
            self._set_status(
                f"Added {added} source(s)"
                + (f" (skipped {skipped} duplicate)" if skipped else "")
            )
        else:
            self._set_status("No new sources added.")

        self._rebuild_source_list()
        self._refresh_source_combo()
        self.customSourceInput.clear()
        self._emit_source_state()

    def _add_builtin_sources(self):
        added = 0
        skipped = 0
        for source in DEFAULT_SOURCES:
            source_id = _normalize_source_id(source.get("id", ""))
            base = str(source.get("base", "")).rstrip("/")
            if not source_id or not base:
                continue
            if source_id in self.searchSources or self._is_duplicate_source_base(base):
                skipped += 1
                continue
            self.searchSources[source_id] = self._coerce_source(source)
            self.sourceOrder.append(source_id)
            added += 1

        if added == 0:
            if skipped:
                self._set_status("No missing built-in sources to add.")
            else:
                self._set_status("Built-in source data missing.")
            return

        self._store_source_health_cache()
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self._set_status(f"Added {added} built-in source(s).")

    def _custom_source_id(self, url: str, name: Optional[str], base: Optional[str] = None) -> str:
        source_base = _normalize_source_id(base or url)
        if not source_base and name:
            source_base = _normalize_source_id(name)
        if not source_base:
            source_base = _normalize_source_id(urlparse(url).netloc or urlparse(url).path or "source")
        return f"custom:{source_base}"

    def _allocate_source_id(self, source_id: str) -> str:
        source_id = _normalize_source_id(source_id)
        if not source_id:
            source_id = "source"

        candidate = source_id
        suffix = 2
        while candidate in self.searchSources:
            candidate = f"{source_id}-{suffix}"
            suffix += 1
        return candidate

    def _make_host_name(self, source: str) -> str:
        parsed = urlparse(source)
        if parsed.netloc:
            if parsed.path and parsed.path.rstrip("/"):
                return f"{parsed.netloc} ({parsed.path.rstrip('/')})"
            return parsed.netloc
        return source

    def _parse_custom_source_entry(self, raw: str) -> Optional[Dict[str, str]]:
        if not raw:
            return None

        raw = raw.strip()
        if raw.startswith("#"):
            return None

        def _coerce_json_source(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
            source = payload.get("url") or payload.get("base") or payload.get("source") or payload.get("source_url")
            if not source:
                return None
            source = self._normalize_source_url(str(source))
            if not source:
                return None

            provided_index = str(payload.get("index", "")).strip()
            parser_raw = str(payload.get("parser", payload.get("mode", ""))).strip()
            parser = _coerce_parser(parser_raw) if parser_raw else None
            if not parser:
                parser = None
            if not parser:
                parser = _coerce_parser(self._infer_source_parser(source, provided_index))

            return {
                "name": str(payload.get("name", "")).strip() or self._make_host_name(source),
                "url": source,
                "index": self._default_index_for_parser(parser, provided_index),
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

        if _looks_like_key_value(raw):
            try:
                if raw.startswith("{") and raw.endswith("}"):
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        parsed = _coerce_json_source(payload)
                        if parsed is not None:
                            return parsed
            except Exception:
                pass

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

            source = self._normalize_source_url(source)
            if not source:
                return None

            index = config.get("index", "").strip()
            parser_raw = str(config.get("parser", config.get("mode", ""))).strip()
            parser = _coerce_parser(parser_raw) if parser_raw else _coerce_parser(
                self._infer_source_parser(source, index)
            )
            name = config.get("name") or self._make_host_name(source)
            index = self._default_index_for_parser(parser, index)
            return {
                "name": name,
                "url": source,
                "index": index,
                "parser": parser,
                "base": source,
            }

        if raw.startswith("{") and raw.endswith("}"):
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    parsed = _coerce_json_source(payload)
                    if parsed is not None:
                        return parsed
            except Exception:
                pass

        parts = [chunk.strip() for chunk in re.split(r"[|,]", raw)]
        parts = [part for part in parts if part]
        if not parts:
            return None

        parser = "fullsort"
        index = "fullsort.gz"
        name: Optional[str] = None
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

        source = self._normalize_source_url(source)
        if not source:
            return None

        if not index:
            index = ""
        parser = _coerce_parser(parser if parser_explicit else self._infer_source_parser(source, index))
        index = self._default_index_for_parser(parser, index)

        return {
            "name": name or self._make_host_name(source),
            "url": source,
            "index": index.strip(),
            "parser": parser,
            "base": source,
        }

    @staticmethod
    def _normalize_source_url(raw: str) -> str:
        parsed = urlparse(str(raw).strip())
        text = str(raw).strip()
        if not parsed.scheme:
            if text.startswith("//"):
                return f"https:{text}"
            if text:
                return f"https://{text}"
            return ""
        return parsed.geturl()

    def _infer_source_parser(self, source_url: str, index: str) -> str:
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

    @staticmethod
    def _default_index_for_parser(parser: str, index: str) -> str:
        parser = _coerce_parser(parser)
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

    def _collect_discovery_seeds(self) -> List[str]:
        if not self.discoverSeedInput.text().strip():
            return []
        seed_lines: List[str] = []
        for raw in re.split(r"[\n,;\s]+", self.discoverSeedInput.text()):
            line = raw.strip()
            if not line:
                continue
            if not re.match(r"^https?://", line, flags=re.I) and not line.startswith("//"):
                line = f"https://{line}"
            seed_lines.append(line)
        return seed_lines

    def _discover_mirrors(self):
        if self._discover_session is not None:
            self._set_status("Mirror discovery already in progress...")
            return

        self._discover_token += 1
        token = self._discover_token
        self._discover_session = token
        self._set_status("Discovering Doomworld idgames mirrors...")
        self._set_controls_enabled(False)

        worker = _DiscoverSourcesWorker(token, seed_urls=self._collect_discovery_seeds())
        worker.signals.finished.connect(self._on_discover_finished)
        worker.signals.failed.connect(self._on_discover_failed)
        self._thread_pool.start(worker)
        if self.discoverSeedInput.text().strip():
            self.discoverSeedInput.clear()

    def _discover_source_id(self, base: str, name: str) -> str:
        normalized = _normalize_source_id(name)
        if not normalized:
            normalized = _normalize_source_id(base)
        if not normalized:
            normalized = "discovered"
        return self._allocate_source_id(f"discovered-{normalized}")

    def _is_duplicate_source_base(self, base: str) -> bool:
        normalized = base.rstrip("/")
        for source in self.searchSources.values():
            if source.get("base", "").rstrip("/") == normalized:
                return True
        return False

    def _on_discover_finished(self, token: int, discovered: list):
        if token != self._discover_session:
            return
        self._discover_session = None
        if not isinstance(discovered, list):
            discovered = []

        added = 0
        updated = 0
        disabled = 0

        for payload in discovered:
            if not isinstance(payload, dict):
                continue

            base = str(payload.get("base", "")).strip()
            if not base:
                continue

            if self._is_duplicate_source_base(base):
                existing = next(
                    (
                        sid
            for sid, item in self.searchSources.items()
                if item.get("base", "").rstrip("/") == base.rstrip("/") and sid.startswith("discovered-")
            ),
            None,
        )
                if existing is not None and existing.startswith("discovered-"):
                    self.searchSources[existing]["enabled"] = bool(payload.get("enabled", True))
                    self.searchSources[existing]["index"] = str(payload.get("index", "fullsort.gz")).strip() or "fullsort.gz"
                    self._set_source_status(
                        existing,
                        SOURCE_STATUS_OK if payload.get("enabled", True) else SOURCE_STATUS_DISABLED,
                        "Mirrors probe updated",
                        persist=True,
                    )
                    updated += 1
                continue

            source_id = self._discover_source_id(base, str(payload.get("name", base)))
            if source_id in self.searchSources:
                continue

            self.searchSources[source_id] = {
                "id": source_id,
                "name": str(payload.get("name", base)),
                "base": base.rstrip("/"),
                "index": str(payload.get("index", "fullsort.gz")).strip() or "fullsort.gz",
                "browser": str(payload.get("browser", base)).rstrip("/"),
                "parser": _coerce_parser(payload.get("parser", "fullsort")),
                "enabled": bool(payload.get("enabled", True)),
                "status": SOURCE_STATUS_OK if bool(payload.get("enabled", True)) else SOURCE_STATUS_DISABLED,
                "status_message": "Discovered and reachable"
                if bool(payload.get("enabled", True))
                else "Discovered but unreachable",
                "status_checked_at": time.time(),
            }
            self.sourceOrder.append(source_id)
            added += 1
            if not bool(payload.get("enabled", True)):
                disabled += 1
                self._source_health[source_id] = {
                    "status": SOURCE_STATUS_DISABLED,
                    "status_message": "Discovered but unreachable",
                    "status_checked_at": time.time(),
                }
            else:
                self._source_health[source_id] = {
                    "status": SOURCE_STATUS_OK,
                    "status_message": "Discovered and reachable",
                    "status_checked_at": time.time(),
                }

        self._store_source_health_cache()

        if added:
            status = f"Discovered {added} new source(s)."
            if disabled:
                status += f" {disabled} mirror(s) unreachable at discovery."
            self._set_status(status)
        elif updated:
            self._set_status("Mirror source reachability updated.")
        else:
            self._set_status("No new Doomworld mirrors discovered.")

        self._rebuild_source_list()
        self._refresh_source_combo()
        self._refresh_controls_for_state()
        self._emit_source_state()

    def _on_discover_failed(self, token: int, error: str):
        if token != self._discover_session:
            return
        self._discover_session = None
        self._refresh_controls_for_state()
        self._set_status(f"Mirror discovery failed: {error}")

    def _set_all_sources_enabled(self, enabled: bool):
        changed = False
        for source_id, source in self.searchSources.items():
            if source.get("enabled") != bool(enabled):
                source["enabled"] = bool(enabled)
                source["status"] = SOURCE_STATUS_OK if enabled else SOURCE_STATUS_DISABLED
                source["status_message"] = "User enabled" if enabled else "User disabled"
                source["status_checked_at"] = time.time()
                self._source_health[source_id] = {
                    "status": source["status"],
                    "status_message": source["status_message"],
                    "status_checked_at": source["status_checked_at"],
                }
                changed = True

        if changed:
            self._store_source_health_cache()
            self._rebuild_source_list()
            self._refresh_source_combo()
            self._emit_source_state()
            self._set_status("Enabled all sources" if enabled else "Disabled all sources")
        else:
            self._set_status("No source state changes.")

    def _clear_custom_source(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if source_id.startswith("custom:"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No user-added source to remove.")
        else:
            self._set_status(f"Removed {removed} user-added source(s).")
            self._store_source_health_cache()
        self._emit_source_state()
        self._refresh_source_list()

    def _purge_unavailable_sources(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if not source_id.startswith(("custom:", "discovered-")):
                continue

            source = self.searchSources.get(source_id)
            if not source:
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1
                continue

            status = source.get("status", SOURCE_STATUS_UNKNOWN)
            if status in {SOURCE_STATUS_UNREACHABLE, SOURCE_STATUS_ERROR}:
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No unavailable user sources to purge.")
        else:
            self._store_source_health_cache()
            self._emit_source_state()
            self._rebuild_source_list()
            self._refresh_source_combo()
            self._set_status(f"Purged {removed} unavailable source(s).")

    def _clear_discovered_sources(self):
        removed = 0
        for source_id in list(self.sourceOrder):
            if source_id.startswith("discovered-"):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1

        if removed == 0:
            self._set_status("No discovered sources to remove.")
        else:
            self._set_status(f"Removed {removed} discovered source(s).")
            self._store_source_health_cache()
        self._emit_source_state()
        self._refresh_source_list()

    def _remove_selected_custom_sources(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a user-added source in the list.")
            return

        removed = 0
        for item in selected:
            source_id = item.data(Qt.UserRole)
            if source_id and (
                source_id.startswith("custom:") or source_id.startswith("discovered-")
            ):
                self.sourceOrder.remove(source_id)
                self.searchSources.pop(source_id, None)
                self._index_cache.pop(source_id, None)
                self._source_health.pop(source_id, None)
                removed += 1
        if removed == 0:
            self._set_status("Only user-added sources can be removed this way.")
        else:
            self._set_status(f"Removed {removed} source(s).")
            self._store_source_health_cache()
        self._emit_source_state()

        self._refresh_source_list()

    def _toggle_selected_source(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source in the list.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        if source_id not in self.searchSources:
            return
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        source = self.searchSources[source_id]
        source["enabled"] = item.checkState() == Qt.Checked
        self._set_source_status(
            source_id,
            SOURCE_STATUS_DISABLED if not source["enabled"] else SOURCE_STATUS_UNKNOWN,
            "User-disabled" if not source["enabled"] else "",
        )
        self._emit_source_state()
        self._set_status(f"Toggled source: {self.searchSources[source_id]['name']}")

    def _reorder_source(self, delta: int):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        idx = self.sourceOrder.index(source_id)
        target = idx + delta
        if target < 0 or target >= len(self.sourceOrder):
            return

        self.sourceOrder[idx], self.sourceOrder[target] = self.sourceOrder[target], self.sourceOrder[idx]
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()

        self.sourceOrderList.setCurrentRow(target)
        self._set_status(f"Moved source: {self.searchSources[source_id]['name']}")

    def _move_source_to_top(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        try:
            idx = self.sourceOrder.index(source_id)
        except ValueError:
            return

        if idx <= 0:
            self._set_status("Source already at top.")
            return

        self.sourceOrder.pop(idx)
        self.sourceOrder.insert(0, source_id)
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self.sourceOrderList.setCurrentRow(0)
        self._set_status(f"Moved source to top: {self.searchSources[source_id]['name']}")

    def _move_source_to_bottom(self):
        selected = self.sourceOrderList.selectedItems()
        if not selected:
            self._set_status("Select a source to move.")
            return

        item = selected[0]
        source_id = item.data(Qt.UserRole)
        try:
            idx = self.sourceOrder.index(source_id)
        except ValueError:
            return

        if idx >= len(self.sourceOrder) - 1:
            self._set_status("Source already at bottom.")
            return

        self.sourceOrder.pop(idx)
        self.sourceOrder.append(source_id)
        self._rebuild_source_list()
        self._refresh_source_combo()
        self._emit_source_state()
        self.sourceOrderList.setCurrentRow(len(self.sourceOrder) - 1)
        self._set_status(f"Moved source to bottom: {self.searchSources[source_id]['name']}")

    def _on_clear_search(self):
        self.searchInput.clear()
        self.resultsTree.clear()
        self._rendered_results = []
        self._set_status("Search cleared.")

    def _on_search(self):
        query = self.searchInput.text().strip().lower()
        source_ids = self._selected_source_ids()

        self.resultsTree.clear()
        if not source_ids:
            self._rendered_results = []
            self._set_status("No source selected.")
            return

        self._rendered_results = []

        self._search_token += 1
        token = self._search_token
        self._search_sessions[token] = {
            "query": query,
            "sources": list(self._iter_source_rows(source_ids)),
            "remaining": set(self._iter_source_rows(source_ids)),
            "results": {},
            "errors": {},
            "ready": False,
        }

        self._set_status("Searching...")
        self._set_controls_enabled(False)

        started = 0
        for source_id in self._search_sessions[token]["sources"]:
            source = self.searchSources.get(source_id)
            if not source:
                self._search_sessions[token]["remaining"].discard(source_id)
                continue

            cached = self._index_cache.get(source_id)
            if self._is_cache_valid(source_id) and cached is not None:
                source["status_message"] = f"Cached index: {_short_age(time.time() - cached.fetched_at)}"
                source["status"] = SOURCE_STATUS_CACHED
                source["status_checked_at"] = cached.fetched_at
                self._set_source_status(
                    source_id,
                    SOURCE_STATUS_CACHED,
                    source["status_message"],
                    persist=True,
                )
                self._on_search_ready(token, source_id, cached.entries)
                continue

            self._set_source_status(source_id, SOURCE_STATUS_CHECKING, "Refreshing index")
            started += 1
            worker = _SearchWorker(token, source_id, source, query=query)
            worker.signals.finished.connect(self._on_search_ready)
            worker.signals.failed.connect(self._on_search_failed)
            self._thread_pool.start(worker)

        if not started:
            self._finalize_search_session(token)

    def _on_search_ready(self, token: int, source_id: str, payload: List[WadBrowserResult]):
        if token != self._search_token:
            return
        session = self._search_sessions.get(token)
        if not session:
            return

        session["remaining"].discard(source_id)

        query = session["query"]
        filtered = self._filter_search_results(payload, query)
        source = self.searchSources.get(source_id)
        if source:
            normalized_payload = list(payload[:MAX_CACHED_INDEX_ENTRIES])
            self._index_cache[source_id] = _IndexCacheEntry(
                source_id=source_id,
                source_name=source["name"],
                fetched_at=time.time(),
                entries=normalized_payload,
            )
            self._store_index_cache(source_id, source, normalized_payload)
        self._set_source_status(
            source_id,
            SOURCE_STATUS_OK,
            f"{len(filtered)} result(s)",
            persist=True,
        )

        session["results"][source_id] = filtered
        self._set_search_progress(token)
        self._render_search_results(token)

        if not session["remaining"]:
            self._finalize_search_session(token)

    def _on_search_failed(self, token: int, source_id: str, error: str):
        if token != self._search_token:
            return
        session = self._search_sessions.get(token)
        if not session:
            return
        session["remaining"].discard(source_id)
        source = self.searchSources.get(source_id, {})
        session["errors"][source_id] = f"{source.get('name', source_id)}: {error}"
        self._set_source_status(
            source_id,
            SOURCE_STATUS_UNREACHABLE,
            error,
            persist=True,
        )
        self._set_search_progress(token)
        self._render_search_results(token)
        if not session["remaining"]:
            self._finalize_search_session(token)

    def _set_search_progress(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return
        total = len(session.get("sources", []))
        if total <= 0:
            return
        remaining = len(session.get("remaining", []))
        completed = total - remaining
        errors = len(session.get("errors", {}))
        if errors:
            self._set_status(f"Searching... {completed}/{total} sources done ({errors} errors).")
        else:
            self._set_status(f"Searching... {completed}/{total} sources done.")

    def _filter_search_results(self, entries: List[WadBrowserResult], query: str) -> List[WadBrowserResult]:
        if not query:
            # Show newest/popular-ish items first by file size while avoiding overload.
            filtered = sorted(entries, key=lambda item: item.size_bytes, reverse=True)
            return filtered[:DEFAULT_SOURCE_LIMIT]

        q = query.strip().lower()
        tokens = [token for token in q.split() if token]
        filtered = []
        for item in entries:
            haystack = f"{item.title} {item.remote_path} {item.description}".lower()
            if all(token in haystack for token in tokens):
                filtered.append(item)
        filtered.sort(
            key=lambda item: (self._search_score(item, q), item.size_bytes),
            reverse=True,
        )
        return filtered[:DEFAULT_SOURCE_LIMIT]

    @staticmethod
    def _search_score(item: WadBrowserResult, query: str) -> int:
        if not query:
            return 0
        normalized = query.strip().lower()
        title = item.title.lower()
        path = item.remote_path.lower()
        description = item.description.lower()
        if normalized in title:
            return 120
        if normalized in path:
            return 80
        if normalized in description:
            return 40
        return 0

    def _render_search_results(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        self.resultsTree.clear()
        self._rendered_results = []
        source_order = session["sources"]
        source_rank = {source_id: rank for rank, source_id in enumerate(source_order)}
        all_results: List[WadBrowserResult] = []
        for source_id in source_order:
            all_results.extend(session["results"].get(source_id, []))

        # Keep order consistent and avoid duplicate remote entries for stability.
        merged: List[WadBrowserResult] = []
        seen = set()
        for item in all_results:
            key = _result_identity(item)
            if not key:
                key = item.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        query = str(session.get("query", "")).strip().lower()
        if query:
            merged.sort(
                key=lambda item: (
                    source_rank.get(item.source_id, len(source_rank) + 1),
                    -self._search_score(item, query),
                    -item.size_bytes,
                    item.title.lower(),
                )
            )
        else:
            merged.sort(
                key=lambda item: (
                    source_rank.get(item.source_id, len(source_rank) + 1),
                    -item.size_bytes,
                    item.title.lower(),
                )
            )

        for item in merged:
            row = QTreeWidgetItem([
                item.source_name,
                item.title,
                _human_size(item.size_bytes),
                item.remote_path,
            ])
            row.setData(0, Qt.UserRole, item)
            row.setData(2, Qt.UserRole, item.size_bytes)
            if item.description:
                row.setToolTip(1, item.description)
            self.resultsTree.addTopLevelItem(row)
        self._rendered_results = merged
        self._on_selection_changed()

    def _finalize_search_session(self, token: int):
        session = self._search_sessions.get(token)
        if not session:
            return

        count = sum(len(items) for items in session["results"].values())
        status = f"Found {count} result(s)."
        if session["errors"]:
            status += " " + "; ".join(sorted(session["errors"].values()))
        if count == 0:
            status = "No matches. Try a broader query or another source."

        self._search_sessions.pop(token, None)
        self._refresh_controls_for_state()
        self._set_status(status)

    def _on_selection_changed(self):
        search_count = len(self.resultsTree.selectedItems())
        local_count = len(self.localTree.selectedItems())
        open_enabled = bool(search_count)
        download_enabled = bool(search_count)
        add_enabled = bool(search_count or local_count)
        delete_enabled = bool(local_count)

        self.openButton.setEnabled(open_enabled)
        self.downloadButton.setEnabled(download_enabled)
        self.downloadAllButton.setEnabled(bool(self._rendered_results))
        self.selectAllResultsButton.setEnabled(self.resultsTree.topLevelItemCount() > 0)
        self.clearResultsSelectionButton.setEnabled(len(self.resultsTree.selectedItems()) > 0)
        self.selectAllLocalButton.setEnabled(self.localTree.topLevelItemCount() > 0)
        self.clearLocalSelectionButton.setEnabled(len(self.localTree.selectedItems()) > 0)
        self.addButton.setEnabled(add_enabled)
        self.addAllButton.setEnabled(bool(self._rendered_results))
        self.deleteButton.setEnabled(delete_enabled)
        self.openLocalButton.setEnabled(delete_enabled)
        self.openLocalFolderButton.setEnabled(delete_enabled)

        # Keep search button available if query is empty or results are visible.
        if self.searchInput.text().strip():
            self.searchButton.setEnabled(True)

    def _selected_search_results(self) -> List[WadBrowserResult]:
        ordered = []
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            if item.isSelected():
                payload = item.data(0, Qt.UserRole)
                if isinstance(payload, WadBrowserResult):
                    ordered.append(payload)
        return ordered

    def _selected_local_files(self) -> List[str]:
        selected: List[str] = []
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            if item.isSelected():
                payload = item.data(0, Qt.UserRole)
                if payload:
                    selected.append(str(payload))
        return selected

    def _add_selected(self):
        selected_files = self._selected_local_files()
        selected_results = self._selected_search_results()
        if not selected_files and not selected_results:
            self._set_status("No selected item to add.")
            return

        added = []
        seen = set()
        for file_path in selected_files:
            if file_path not in seen:
                added.append(file_path)
                seen.add(file_path)

        missing = []
        for result in selected_results:
            candidate = self._safe_library_path(result)
            if str(candidate) in seen:
                continue
            if candidate.exists():
                added.append(str(candidate))
                seen.add(str(candidate))
            else:
                missing.append(result)

        if added:
            self.addRequested.emit(added)

        if missing:
            self._start_download_session(missing, auto_add=True)
        elif added:
            self._set_status("All selected mods are already downloaded and added.")
        else:
            self._set_status("No downloadable or local selection available.")

    def _add_all_results(self):
        if not self._rendered_results:
            self._set_status("No search results to add.")
            return
        self._start_download_session(self._rendered_results, auto_add=True)

    def _download_all_results(self):
        if not self._rendered_results:
            self._set_status("No search results to download.")
            return
        self._start_download_session(self._rendered_results, auto_add=False)

    def _select_all_results(self):
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            item.setSelected(True)

    def _clear_results_selection(self):
        for idx in range(self.resultsTree.topLevelItemCount()):
            item = self.resultsTree.topLevelItem(idx)
            item.setSelected(False)

    def _select_all_local(self):
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            item.setSelected(True)

    def _clear_local_selection(self):
        for idx in range(self.localTree.topLevelItemCount()):
            item = self.localTree.topLevelItem(idx)
            item.setSelected(False)

    def _download_selected(self):
        selected_results = self._selected_search_results()
        if not selected_results:
            self._set_status("No remote selection to download.")
            return
        self._start_download_session(selected_results, auto_add=False)

    def _safe_library_path(self, result: WadBrowserResult) -> Path:
        return _safe_library_name(result.remote_path, result.source_id, self.library_dir)

    def _metadata_path(self, destination: str) -> Path:
        destination_path = Path(destination)
        return destination_path.with_suffix(destination_path.suffix + ".bfg-meta.json")

    def _write_metadata(self, destination: str, result: WadBrowserResult):
        payload = {
            "title": result.title,
            "description": result.description,
            "source_id": result.source_id,
            "source_name": result.source_name,
            "remote_path": result.remote_path,
            "download_url": result.download_url,
            "browser_url": result.browser_url,
            "downloaded_at": int(time.time()),
            "size_bytes": result.size_bytes,
        }
        try:
            self._metadata_path(destination).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _read_metadata(self, destination: str) -> Dict[str, Any]:
        path = self._metadata_path(destination)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _collect_library_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.library_dir.glob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in DEFAULT_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            metadata = self._read_metadata(str(path))
            rows.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": size,
                    "size_text": _human_size(size),
                    "source": metadata.get("source_name") or "local",
                    "source_path": metadata.get("remote_path") or "-",
                    "source_id": metadata.get("source_id") or "local",
                    "title": metadata.get("title") or path.name,
                }
            )

        rows.sort(key=lambda row: (row["name"].lower(), row["size"]))
        self._library_rows = rows
        return rows

    def _library_row_matches(self, row: Dict[str, Any], query: str) -> bool:
        tokens = [token for token in str(query).lower().split() if token]
        if not tokens:
            return True

        haystack = (
            f"{row.get('name', '')} "
            f"{row.get('title', '')} "
            f"{row.get('source', '')} "
            f"{row.get('source_path', '')} "
            f"{row.get('source_id', '')}"
        ).lower()
        return all(token in haystack for token in tokens)

    def _render_library_rows(self, rows: List[Dict[str, Any]], keep_selected: Optional[Iterable[str]] = None):
        selected = set(str(path) for path in (keep_selected or []))
        self.localTree.clear()
        for row in rows:
            item = QTreeWidgetItem([
                row.get("name", ""),
                row.get("size_text", "—"),
                row.get("source", "local"),
                row.get("source_path", "-"),
            ])
            file_path = row.get("path", "")
            item.setData(0, Qt.UserRole, file_path)
            item.setToolTip(0, str(file_path))
            if file_path in selected:
                item.setSelected(True)
            self.localTree.addTopLevelItem(item)

    def _start_download_session(self, results: List[WadBrowserResult], auto_add: bool):
        if not results:
            return

        deduped: List[WadBrowserResult] = []
        seen = set()
        for result in results:
            key = _result_identity(result)
            if not key:
                key = result.download_url.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)

        self._download_token += 1
        token = self._download_token
        by_url = {r.download_url: r for r in deduped}
        ordered_urls = [r.download_url for r in deduped]
        self._download_sessions[token] = {
            "auto_add": bool(auto_add),
            "pending": 0,
            "paths": [],
            "failures": [],
            "failed_results": [],
            "by_url": by_url,
            "ordered_urls": ordered_urls,
            "path_by_url": {},
        }
        self._failed_downloads = []
        self._refresh_retry_state()
        self._set_controls_enabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        for result in deduped:
            target = self._safe_library_path(result)
            if target.exists():
                # Avoid duplicate downloads.
                existing = self._download_sessions[token]
                existing["paths"].append(str(target))
                existing["path_by_url"][result.download_url] = str(target)
                self._write_metadata(str(target), result)
                continue

            self._download_sessions[token]["pending"] += 1
            worker = _DownloadWorker(token, result.download_url, str(target))
            worker.signals.finished.connect(self._on_download_finished)
            worker.signals.failed.connect(self._on_download_failed)
            worker.signals.progress.connect(self._on_download_progress)
            self._thread_pool.start(worker)

        if self._download_sessions[token]["pending"] == 0:
            self._finish_download_session(token)

    def _on_download_progress(self, token: int, received: int, total: int, url: str):
        if token not in self._download_sessions:
            return
        if total:
            self._set_status(f"Downloading ({Path(url).name}) {received}/{total} bytes ({received / total:.0%})")

    def _finish_download_session(self, session_id: int):
        payload = self._download_sessions.pop(session_id, None)
        if payload is None:
            return

        QApplication.restoreOverrideCursor()
        self._refresh_controls_for_state()
        self._refresh_local_library()

        ordered_urls = payload.get("ordered_urls", [])
        path_by_url = payload.get("path_by_url", {})
        ordered_paths = [path_by_url.get(url) for url in ordered_urls if path_by_url.get(url)]
        payload["paths"] = ordered_paths
        count = len(payload.get("paths", []))
        failures = payload.get("failures", [])

        if payload.get("auto_add") and payload.get("paths"):
            self.addRequested.emit(list(payload["paths"]))

        if failures:
            self._set_status(f"Downloads complete. {count} added. {len(failures)} failed.")
        elif count:
            if payload.get("auto_add"):
                self._set_status(f"Downloaded and added {count} mod(s).")
            else:
                self._set_status(f"Downloaded {count} mod(s) to {self.library_dir}")
        else:
            self._set_status(f"No files needed download. Added local files if available.")
        self._update_failed_downloads(payload)
        self._refresh_retry_state()

    def _on_download_finished(self, token: int, destination: str, source_url: str):
        session = self._download_sessions.get(token)
        if not session:
            return

        session["pending"] -= 1
        session["paths"].append(destination)
        session["path_by_url"][source_url] = destination

        result = session.get("by_url", {}).get(source_url)
        if result is None:
            result = WadBrowserResult(
                title=Path(destination).name,
                description="",
                source_id="direct",
                source_name="Downloaded",
                size_bytes=0,
                remote_path=Path(destination).name,
                download_url=source_url,
                browser_url=source_url,
            )
        self._write_metadata(destination, result)

        if session["pending"] <= 0:
            self._finish_download_session(token)

    def _on_download_failed(self, token: int, source_url: str, error: str):
        session = self._download_sessions.get(token)
        if not session:
            return
        session["pending"] -= 1
        session["failures"].append(f"{source_url}: {error}")
        result = session.get("by_url", {}).get(source_url)
        if result is not None:
            session["failed_results"].append(result)
        if session["pending"] <= 0:
            self._finish_download_session(token)

    def _retry_failed_downloads(self):
        if self._is_busy():
            self._set_status("Cannot retry while a session is running.")
            return

        if not self._failed_downloads:
            self._set_status("No failed downloads to retry.")
            return

        pending = list(self._failed_downloads)
        self._start_download_session(pending, auto_add=self._failed_download_auto_add)

    def _open_selected_remote(self):
        selected = self._selected_search_results()
        if not selected:
            self._set_status("No search item selected to open.")
            return
        for entry in selected:
            if entry and entry.browser_url:
                webbrowser.open(entry.browser_url)

    def _open_selected_local_file(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        self._open_paths(selected)

    def _delete_selected_local(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        response = QMessageBox.question(
            self,
            "Delete Mod Files",
            f"Delete {len(selected)} selected file(s)?\n\nThis action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            self._set_status("Delete cancelled.")
            return

        deleted = 0
        deleted_paths = []
        for file_path in selected:
            p = Path(file_path)
            try:
                if p.is_file():
                    p.unlink()
                    meta = self._metadata_path(str(p))
                    if meta.exists():
                        meta.unlink()
                    deleted += 1
                    deleted_paths.append(file_path)
            except OSError as exc:
                self._set_status(f"Could not delete {file_path}: {exc}")
        self._refresh_local_library()
        if deleted:
            self._set_status(f"Deleted {deleted} file(s).")
            self.removedRequested.emit(deleted_paths)

    def _refresh_local_library(self):
        selected = self._selected_local_files()
        rows = self._collect_library_rows()
        query = getattr(self, "localFilterInput", None)
        query_text = str(query.text()).strip() if query is not None else ""

        filtered = [row for row in rows if self._library_row_matches(row, query_text)]
        self._render_library_rows(filtered, keep_selected=selected)

        if query_text:
            self._set_status(
                f"Library: {len(filtered)} of {len(rows)} matched in {self.library_dir}"
            )
        else:
            self._set_status(f"Library: {len(rows)} managed files in {self.library_dir}")

    def _open_library_dir(self):
        self.library_dir.mkdir(parents=True, exist_ok=True)
        webbrowser.open(self.library_dir.as_uri())

    def _open_selected_local_folder(self):
        selected = self._selected_local_files()
        if not selected:
            self._set_status("No local file selected.")
            return

        opened = set()
        for file_path in selected:
            parent = Path(file_path).expanduser().parent.resolve()
            if parent in opened:
                continue
            webbrowser.open(parent.as_uri())
            opened.add(parent)

    def _open_paths(self, paths: List[str]):
        for file_path in paths:
            path = Path(file_path).expanduser()
            if not path.exists():
                self._set_status(f"File missing: {path}")
                continue
            webbrowser.open(path.resolve().as_uri())
