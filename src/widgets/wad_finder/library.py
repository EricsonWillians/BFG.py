"""Local-library helpers for the WAD finder (Qt-free).

All functions take explicit paths/dirs instead of widget state so they can be
unit-tested against temporary directories.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .constants import DEFAULT_EXTENSIONS
from .models import WadBrowserResult
from .text import human_size, normalize_remote_path


def safe_library_name(path: str, source_id: str, target_dir: Path, reserved: set | None = None) -> Path:
    file_name = os.path.basename(path)
    if not file_name:
        file_name = f"mod_{source_id}.pkg"
    candidate = target_dir / file_name
    if not candidate.exists() and (reserved is None or str(candidate).lower() not in reserved):
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    idx = 1
    while True:
        alt = target_dir / f"{stem}_{idx}__{source_id}{suffix}"
        if not alt.exists() and (reserved is None or str(alt).lower() not in reserved):
            return alt
        idx += 1


def metadata_path(destination: str) -> Path:
    destination_path = Path(destination)
    return destination_path.with_suffix(destination_path.suffix + ".bfg-meta.json")


def write_metadata(destination: str, result: WadBrowserResult):
    payload = {
        "title": result.title,
        "description": result.description,
        "metadata_text": result.metadata_text,
        "source_id": result.source_id,
        "source_name": result.source_name,
        "remote_path": result.remote_path,
        "download_url": result.download_url,
        "browser_url": result.browser_url,
        "downloaded_at": int(time.time()),
        "size_bytes": result.size_bytes,
    }
    with suppress(OSError):
        target = metadata_path(destination)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)


def read_metadata(destination: str) -> dict[str, Any]:
    path = metadata_path(destination)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_library_identity_map(library_dir: Path) -> dict[tuple, Path]:
    identity_map: dict[tuple, Path] = {}
    for candidate in library_dir.glob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        metadata = read_metadata(str(candidate))
        metadata_remote = normalize_remote_path(str(metadata.get("remote_path", ""))).lower()
        metadata_source = str(metadata.get("source_id", "")).lower()
        if metadata_remote:
            identity_map.setdefault((metadata_remote, metadata_source), candidate)
    return identity_map


def safe_library_path(
    result: WadBrowserResult,
    library_dir: Path,
    reserved: set | None = None,
    identity_map: dict[tuple, Path] | None = None,
) -> Path:
    remote_identity = normalize_remote_path(result.remote_path).lower()
    if remote_identity:
        if identity_map is None:
            identity_map = build_library_identity_map(library_dir)
        candidate = identity_map.get((remote_identity, result.source_id.lower()))
        if candidate is not None and (reserved is None or str(candidate).lower() not in reserved):
            return candidate
    return safe_library_name(result.remote_path, result.source_id, library_dir, reserved)


def collect_library_rows(library_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(library_dir.glob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        metadata = read_metadata(str(path))
        downloaded_at = metadata.get("downloaded_at")
        try:
            installed_text = time.strftime("%Y-%m-%d", time.localtime(int(downloaded_at)))
        except (TypeError, ValueError, OSError, OverflowError):
            installed_text = "local"
        rows.append(
            {
                "path": str(path),
                "name": path.name,
                "size": size,
                "size_text": human_size(size),
                "source": metadata.get("source_name") or "local",
                "source_path": metadata.get("remote_path") or "-",
                "source_id": metadata.get("source_id") or "local",
                "title": metadata.get("title") or path.name,
                "description": metadata.get("description") or "",
                "metadata_text": metadata.get("metadata_text") or "",
                "downloaded_at": downloaded_at or 0,
                "installed_text": installed_text,
            }
        )

    rows.sort(key=lambda row: (row["name"].lower(), row["size"]))
    return rows


def library_row_matches(row: dict[str, Any], query: str) -> bool:
    tokens = [token for token in str(query).lower().split() if token]
    if not tokens:
        return True

    haystack = (
        f"{row.get('name', '')} "
        f"{row.get('title', '')} "
        f"{row.get('description', '')} "
        f"{row.get('metadata_text', '')} "
        f"{row.get('source', '')} "
        f"{row.get('source_path', '')} "
        f"{row.get('source_id', '')}"
    ).lower()
    return all(token in haystack for token in tokens)


def sweep_orphan_parts(library_dir: Path):
    """Remove partial downloads orphaned by killed/crashed sessions."""
    with suppress(OSError):
        for entry in library_dir.iterdir():
            if ".part" in entry.suffix or entry.name.endswith(".part"):
                with suppress(OSError):
                    entry.unlink()
