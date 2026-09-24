"""Qt worker layer for the WAD finder: QRunnable search/download/discovery.

All parsing lives in ``parsing.py``; these classes only own the network I/O,
the safety limits, and the Qt signal plumbing.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import shutil
import threading
import time
import zipfile

from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

from .constants import (
    DEFAULT_SOURCE_PARSER,
    DEFAULT_SOURCES,
    IDGAMES_API_TIMEOUT,
    IDGAMES_TEXTFILE_MAX_FETCHES,
    IDGAMES_TEXTFILE_MIN_TOKENS,
    MAX_INDEX_DECOMPRESSED_BYTES,
    MAX_INDEX_FETCH_BYTES,
    MIRRORS_DISCOVERY_API_URLS,
    MIRRORS_DISCOVERY_MAX_DEPTH,
    MIRRORS_DISCOVERY_MAX_PAGES,
    MIRRORS_DISCOVERY_TIMEOUT,
    MIRRORS_DISCOVERY_URLS,
    SOURCE_PING_TIMEOUT,
    UA,
)
from .models import WadBrowserResult
from .parsing import (
    apply_textfile_metadata,
    candidate_textfile_urls,
    coerce_int,
    collect_api_items,
    crawl_html,
    crawl_start_path,
    iter_html_links,
    link_targets,
    looks_like_datetime_token,
    normalize_api_path,
    parse_fullsort,
    parse_fullsort_tokens,
    parse_html,
    parse_idgames_api,
    parse_json,
    parse_rss,
    parse_text,
    parse_textfile_metadata,
    relative_path_for,
    textfile_enrichment_candidates,
)
from .sources import is_idgames_source, normalize_parser
from .text import query_matches, result_search_blob, tokenize_query

# Backward-compatible legacy (underscore-prefixed) names used by the worker
# bodies below, kept from the pre-split monolith.
_coerce_parser = normalize_parser
_tokenize_query = tokenize_query
_query_matches = query_matches
_result_search_blob = result_search_blob

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
        # One session per run: discovery crawls issue many requests, so keep
        # connections alive instead of re-handshaking per page/probe.
        self._session = requests.Session()

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

    def _discover_from_api(self, seed_url: str) -> List[dict]:
        discovered = []
        try:
            response = self._session.get(
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

    def _head_probe_index(self, base: str) -> tuple[str, bool]:
        base = base.rstrip("/")
        index_candidates = ("fullsort.gz", "fullsort")
        for candidate in index_candidates:
            url = f"{base}/{candidate}"
            for method in ("head", "get"):
                response = None
                try:
                    response = self._session.request(
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
                response = self._session.get(
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
    def __init__(
        self,
        token: int,
        source_id: str,
        source: Dict[str, str],
        query: str = "",
        seed_results: Optional[List[WadBrowserResult]] = None,
    ):
        super().__init__()
        self.token = token
        self.source_id = source_id
        self.source = source
        self.query = query.lower().strip()
        self.seed_results = seed_results
        self._seen_textfile_urls = set()
        self.signals = _SearchSignals()
        # One session per run: a search may fetch several indexes plus
        # textfile enrichment payloads; reuse connections for all of them.
        self._session = requests.Session()

    def _fetch_index(self, url: str) -> str:
        # Bounded read: mirrors are auto-discovered/user-editable, so an
        # oversized or gzip-bombed index must not exhaust memory.
        with self._session.get(
            url,
            headers={"User-Agent": UA},
            timeout=30,
            stream=True,
        ) as response:
            response.raise_for_status()
            chunks = []
            read = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                read += len(chunk)
                if read > MAX_INDEX_FETCH_BYTES:
                    raise RuntimeError("index response exceeds the maximum allowed size")
                chunks.append(chunk)
        data = b"".join(chunks)
        if data.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                data = gz.read(MAX_INDEX_DECOMPRESSED_BYTES + 1)
            if len(data) > MAX_INDEX_DECOMPRESSED_BYTES:
                raise RuntimeError("decompressed index exceeds the maximum allowed size")
        return data.decode("utf-8", errors="ignore")

    def _fetch_api(self, base: str, query: str) -> str:
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
                response = self._session.get(
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

    _is_idgames_source = staticmethod(is_idgames_source)
    _candidate_textfile_urls = staticmethod(candidate_textfile_urls)
    _parse_textfile_metadata = staticmethod(parse_textfile_metadata)

    def _enrich_with_text_metadata(
        self,
        query: str,
        results: List[WadBrowserResult],
        source: Optional[Dict[str, str]] = None,
    ) -> int:
        if not query:
            return 0
        source = source or self.source
        if not self._is_idgames_source(source):
            return 0

        tokens = _tokenize_query(query)
        if len(tokens) < IDGAMES_TEXTFILE_MIN_TOKENS:
            return 0

        enriched = 0
        candidate_items = textfile_enrichment_candidates(query, results)
        budget = min(IDGAMES_TEXTFILE_MAX_FETCHES, len(candidate_items))
        multi_token_query = len(tokens) >= 2
        deadline = time.time() + (15.0 if multi_token_query else 4.0)
        attempt_budget = min(budget, 90 if multi_token_query else 12)
        success_budget = min(budget, 36 if multi_token_query else 12)
        attempts = 0

        for item in candidate_items:
            if attempts >= attempt_budget:
                break
            if enriched >= success_budget:
                break
            if time.time() > deadline:
                break

            haystack = _result_search_blob(item)
            if _query_matches(haystack, self.query):
                continue

            for candidate in self._candidate_textfile_urls(source, item):
                if candidate in self._seen_textfile_urls:
                    continue

                self._seen_textfile_urls.add(candidate)
                try:
                    attempts += 1
                    response = self._session.get(
                        candidate,
                        headers={"User-Agent": UA},
                        timeout=(1.5, 3.0),
                    )
                    if response.status_code >= 400:
                        continue

                    payload = self._parse_textfile_metadata(response.content.decode("utf-8", errors="ignore"))
                    if not apply_textfile_metadata(item, payload):
                        continue
                    enriched += 1
                    break
                except Exception:
                    continue

        return enriched

    @staticmethod
    def _iter_idgames_fallback_sources(source: Dict[str, str]) -> List[Dict[str, str]]:
        fallback_sources: List[Dict[str, str]] = []
        primary_base = str(source.get("base", "")).strip().rstrip("/").lower()
        if not primary_base:
            return fallback_sources

        for entry in DEFAULT_SOURCES:
            base = str(entry.get("base", "")).strip().rstrip("/")
            if not base:
                continue
            if base.lower() == primary_base:
                continue
            if "idgames" not in base.lower():
                continue
            if entry.get("parser") not in {"fullsort", "html", "auto", "idgames_api"}:
                continue
            fallback_sources.append(
                {
                    "base": base,
                    "browser": str(entry.get("browser", base)).strip(),
                    "index": str(entry.get("index", "")).strip(),
                    "name": str(entry.get("name", base)),
                    "id": str(entry.get("id", "")),
                    "parser": str(entry.get("parser", "fullsort")).strip(),
                }
            )

        return fallback_sources

    def _preferred_text_metadata_source(self) -> Dict[str, str]:
        primary_base = str(self.source.get("base", "")).strip().rstrip("/")
        if primary_base and "doomworld.com" not in primary_base.lower():
            return self.source

        for candidate in self._iter_idgames_fallback_sources(self.source):
            candidate_base = str(candidate.get("base", "")).strip().rstrip("/")
            if candidate_base and "doomworld.com" not in candidate_base.lower():
                return candidate
        return self.source

    _coerce_int = staticmethod(coerce_int)
    _normalize_api_path = staticmethod(normalize_api_path)
    _collect_api_items = staticmethod(collect_api_items)
    _parse_idgames_api = staticmethod(parse_idgames_api)
    _parse_json = staticmethod(parse_json)
    _parse_rss = staticmethod(parse_rss)
    _iter_html_links = staticmethod(iter_html_links)
    _link_targets = staticmethod(link_targets)
    _relative_path_for = staticmethod(relative_path_for)
    _parse_html = staticmethod(parse_html)
    _crawl_start_path = staticmethod(crawl_start_path)
    _looks_like_datetime_token = staticmethod(looks_like_datetime_token)
    _parse_fullsort_tokens = staticmethod(parse_fullsort_tokens)
    _parse_fullsort = staticmethod(parse_fullsort)
    _parse_text = staticmethod(parse_text)

    def _crawl_html(
        self,
        source: Dict[str, str],
        start_path: str,
        query: str,
    ) -> List[tuple[int, str, str]]:
        return crawl_html(source, start_path, query, fetch=self._fetch_index)

    def run(self):
        try:
            parser = _coerce_parser(self.source.get("parser", DEFAULT_SOURCE_PARSER))
            start = str(self.source.get("index", "")).strip()
            if not start:
                start = "fullsort.gz"
            base = self.source["base"].rstrip("/")
            source = self.source
            query = self.query

            if self.seed_results is not None:
                parsed = [replace(item) for item in self.seed_results]
                self._enrich_with_text_metadata(query, parsed, source=source)
                self.signals.finished.emit(self.token, self.source_id, parsed)
                return

            raw_items: List[Any] = []
            seen_remote_paths = set()

            def _collect_raw(items: Any) -> None:
                for item in items or []:
                    if isinstance(item, WadBrowserResult):
                        remote = item.remote_path
                    elif isinstance(item, (list, tuple)):
                        if len(item) < 2:
                            continue
                        remote = item[1]
                    else:
                        continue

                    remote = _SearchWorker._normalize_api_path(str(remote))
                    if not remote or remote in seen_remote_paths:
                        continue
                    seen_remote_paths.add(remote)
                    raw_items.append(item)

            if parser == "auto":
                if query:
                    candidate_sources = [self.source]
                    if self._is_idgames_source(self.source):
                        candidate_sources.extend(self._iter_idgames_fallback_sources(self.source))

                    if self._is_idgames_source(self.source):
                        for candidate in candidate_sources:
                            source = candidate
                            base = str(candidate.get("base", "")).rstrip("/")
                            if not base:
                                continue
                            try:
                                text = self._fetch_index(f"{base}/fullsort.gz")
                                _collect_raw(self._parse_fullsort(text))
                            except Exception:
                                pass

                        if not raw_items:
                            for candidate in candidate_sources:
                                source = candidate
                                base = str(candidate.get("base", "")).rstrip("/")
                                if not base:
                                    continue
                                try:
                                    text = self._fetch_index(f"{base}/{start}")
                                    _collect_raw(self._parse_fullsort(text))
                                    if not raw_items:
                                        _collect_raw(self._parse_json(candidate, text, ""))
                                    if not raw_items:
                                        _collect_raw(self._parse_rss(candidate, text, ""))
                                except Exception:
                                    pass

                        if not raw_items:
                            for candidate in candidate_sources:
                                source = candidate
                                crawl_start = self._crawl_start_path(start)
                                _collect_raw(self._crawl_html(candidate, crawl_start, ""))

                    else:
                        try:
                            text = self._fetch_index(f"{base}/{start}")
                            raw_items = self._parse_fullsort(text)
                            if not raw_items:
                                raw_items = self._parse_json(self.source, text, "")
                            if not raw_items:
                                raw_items = self._parse_rss(self.source, text, "")
                        except Exception:
                            crawl_start = self._crawl_start_path(start)
                            raw_items = self._crawl_html(self.source, crawl_start, "")

                else:
                    try:
                        text = self._fetch_index(f"{base}/{start}")
                        raw_items = self._parse_fullsort(text)
                        if not raw_items:
                            raw_items = self._parse_json(self.source, text, query)
                        if not raw_items:
                            raw_items = self._parse_rss(self.source, text, query)
                    except Exception:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, "")
            elif parser == "idgames_api":
                if query:
                    candidate_sources = [self.source] + self._iter_idgames_fallback_sources(self.source)
                    if not raw_items:
                        for candidate in candidate_sources:
                            source = candidate
                            base = str(candidate.get("base", "")).rstrip("/")
                            try:
                                text = self._fetch_index(f"{base}/fullsort.gz")
                                _collect_raw(self._parse_fullsort(text))
                            except Exception:
                                pass

                    if not raw_items:
                        for candidate in candidate_sources:
                            source = candidate
                            base = str(candidate.get("base", "")).rstrip("/")
                            try:
                                _collect_raw(
                                    self._parse_idgames_api(
                                        candidate,
                                        self._fetch_api(base, query),
                                        query,
                                        allow_empty_query=False,
                                        filter_by_query=False,
                                    )
                                )
                            except Exception:
                                pass

                    if not raw_items:
                        for candidate in candidate_sources:
                            source = candidate
                            crawl_start = self._crawl_start_path(start)
                            _collect_raw(self._crawl_html(candidate, crawl_start, ""))
                else:
                    candidate_sources = [self.source] + self._iter_idgames_fallback_sources(self.source)
                    text = ""
                    for candidate in candidate_sources:
                        source = candidate
                        base = str(candidate.get("base", "")).rstrip("/")
                        try:
                            text = self._fetch_index(f"{base}/fullsort.gz")
                            _collect_raw(self._parse_fullsort(text))
                        except Exception:
                            pass
                    if not text:
                        text = ""

                    if not raw_items:
                        for candidate in candidate_sources:
                            source = candidate
                            base = str(candidate.get("base", "")).rstrip("/")
                            try:
                                text = self._fetch_index(f"{base}/{start}")
                                _collect_raw(self._parse_fullsort(text))
                                if not raw_items:
                                    _collect_raw(self._parse_json(candidate, text, ""))
                                if not raw_items:
                                    _collect_raw(self._parse_rss(candidate, text, ""))
                            except Exception:
                                pass
                    if not raw_items:
                        for candidate in candidate_sources:
                            source = candidate
                            crawl_start = self._crawl_start_path(start)
                            _collect_raw(self._crawl_html(candidate, crawl_start, ""))
            elif parser == "json":
                text = self._fetch_index(f"{base}/{start}")
                raw_items = self._parse_json(self.source, text, "")
                if not raw_items and query:
                    try:
                        raw_items = self._parse_idgames_api(
                            self.source,
                            self._fetch_api(base, query),
                            query,
                            allow_empty_query=False,
                            filter_by_query=False,
                        )
                    except Exception:
                        crawl_start = self._crawl_start_path(start)
                        raw_items = self._crawl_html(self.source, crawl_start, "")
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, "")
            elif parser == "html":
                candidate_sources = [self.source]
                if self._is_idgames_source(self.source):
                    candidate_sources.extend(self._iter_idgames_fallback_sources(self.source))
                for candidate in candidate_sources:
                    source = candidate
                    base = str(candidate.get("base", "")).rstrip("/")
                    if not base:
                        continue
                    if self._is_idgames_source(candidate):
                        try:
                            text = self._fetch_index(f"{base}/fullsort.gz")
                            _collect_raw(self._parse_fullsort(text))
                        except Exception:
                            pass

                if not raw_items:
                    for candidate in candidate_sources:
                        source = candidate
                        crawl_start = self._crawl_start_path(str(candidate.get("index", start)))
                        _collect_raw(self._crawl_html(candidate, crawl_start, ""))
            elif parser == "rss":
                text = self._fetch_index(f"{base}/{start}")
                raw_items = self._parse_rss(self.source, text, "")
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, "")
            elif parser == "text":
                index_url = urljoin(base + "/", start)
                text = self._fetch_index(index_url)
                raw_items = self._parse_text(text, self.source, "")
                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, "")
            else:
                sources = [self.source]
                if parser == "fullsort" and self._is_idgames_source(self.source):
                    sources.extend(self._iter_idgames_fallback_sources(self.source))
                aggregate_fullsort = parser == "fullsort" and bool(self.query)

                for candidate in sources:
                    candidate_base = str(candidate.get("base", "")).rstrip("/")
                    if not candidate_base:
                        continue
                    candidate_index = str(candidate.get("index", "")).strip() or start
                    if not candidate_index:
                        continue
                    index_url = urljoin(candidate_base + "/", candidate_index)
                    try:
                        text = self._fetch_index(index_url)
                        parsed = self._parse_fullsort(text)
                        if parsed:
                            if aggregate_fullsort:
                                _collect_raw(parsed)
                            else:
                                raw_items[:] = parsed
                                break
                            continue

                        parsed = self._parse_json(candidate, text, "")
                        if parsed:
                            if aggregate_fullsort:
                                _collect_raw(parsed)
                                continue
                            raw_items[:] = parsed
                            break

                        parsed = self._parse_rss(candidate, text, "")
                        if parsed:
                            if aggregate_fullsort:
                                _collect_raw(parsed)
                                continue
                            raw_items[:] = parsed
                            break
                        if raw_items and not aggregate_fullsort:
                            break
                    except Exception:
                        continue

                if not raw_items:
                    crawl_start = self._crawl_start_path(start)
                    raw_items = self._crawl_html(self.source, crawl_start, "")

            parsed: List[WadBrowserResult] = []
            for item in raw_items:
                if isinstance(item, WadBrowserResult):
                    parsed.append(item)
                    continue

                size = int(item[0])
                path = item[1]
                description = item[2] if len(item) > 2 else ""
                parsed.append(
                    WadBrowserResult(
                        title=Path(path).name,
                        description=description,
                        metadata_text=description,
                        source_id=self.source_id,
                        source_name=source.get("name", source.get("base", "")),
                        size_bytes=size,
                        remote_path=path,
                        download_url=urljoin(source.get("base", "").rstrip("/") + "/", path),
                        browser_url=urljoin(source.get("browser", source.get("base", "")).rstrip("/") + "/", path),
                    )
                )

            enrichment_source = self._preferred_text_metadata_source()
            self._enrich_with_text_metadata(query, parsed, source=enrichment_source)
            self.signals.finished.emit(self.token, self.source_id, parsed)
        except Exception as exc:  # pragma: no cover - network path
            self.signals.failed.emit(self.token, self.source_id, str(exc))


class _DownloadWorker(QRunnable):
    # Mirrors are auto-discovered from crawled pages and user-editable, so
    # downloads must be bounded: a hostile/broken mirror must not fill the disk.
    MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
    # One Qt signal per 64 KiB chunk floods the event loop on big downloads;
    # throttle progress emissions and always emit the terminal progress.
    PROGRESS_EMIT_INTERVAL = 0.1  # seconds

    def __init__(self, token: int, url: str, destination: str):
        super().__init__()
        self.token = token
        self.url = url
        self.destination = destination
        self.signals = _DownloadSignals()
        self._cancel_event = threading.Event()

    def cancel(self):
        """Ask the worker to stop; the chunk loop aborts at the next chunk.

        Thread-safe: may be called from any thread. Cancellation is
        cooperative, so it takes effect on the next received chunk and is
        reported through the ``failed`` signal like any other failure.
        """
        self._cancel_event.set()

    @pyqtSlot()
    def run(self):
        # Per-process temp name: two BFG instances sharing a library dir must
        # not clobber each other's partial downloads.
        tmp = f"{self.destination}.{os.getpid()}.part"
        try:
            total = 0
            with requests.get(
                self.url,
                stream=True,
                headers={"User-Agent": UA},
                timeout=120,
            ) as response:
                response.raise_for_status()

                total = int(response.headers.get("Content-Length", 0) or 0)
                if total > self.MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        f"download too large ({total} bytes; limit is {self.MAX_DOWNLOAD_BYTES})"
                    )
                if total > 0:
                    try:
                        free = shutil.disk_usage(
                            str(Path(self.destination).expanduser().parent)
                        ).free
                        if total > free:
                            raise RuntimeError("not enough free disk space for this download")
                    except OSError:
                        pass  # cannot stat the filesystem; proceed and let writes fail
                downloaded = 0
                last_progress_emit = 0.0
                with open(tmp, "wb") as fp:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        if self._cancel_event.is_set():
                            raise RuntimeError("download cancelled by user")
                        downloaded += len(chunk)
                        if downloaded > self.MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("download exceeded the maximum allowed size")
                        fp.write(chunk)
                        now = time.monotonic()
                        if now - last_progress_emit >= self.PROGRESS_EMIT_INTERVAL:
                            last_progress_emit = now
                            self.signals.progress.emit(self.token, downloaded, total, self.url)
                if downloaded <= 0:
                    raise RuntimeError("server returned an empty file")
                # Terminal progress update, so the UI always sees the final byte count.
                self.signals.progress.emit(self.token, downloaded, total, self.url)
                suffix = Path(self.destination).suffix.lower()
                if suffix == ".wad":
                    with open(tmp, "rb") as fp:
                        if fp.read(4) not in {b"IWAD", b"PWAD"}:
                            raise RuntimeError("download is not a valid WAD file")
                elif suffix in {".zip", ".pk3", ".ipk3", ".pk7", ".pke"}:
                    if not zipfile.is_zipfile(tmp):
                        raise RuntimeError("download is not a valid ZIP-based mod package")
                os.replace(tmp, self.destination)
            self.signals.finished.emit(self.token, self.destination, self.url)
        except Exception as exc:  # pragma: no cover - network path
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.signals.failed.emit(self.token, self.url, str(exc))
