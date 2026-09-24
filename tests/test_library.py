"""Unit tests for src.widgets.wad_finder.library (pure, Qt-free, tmp dirs)."""

import json

from src.widgets.wad_finder.library import (
    build_library_identity_map,
    collect_library_rows,
    library_row_matches,
    metadata_path,
    read_metadata,
    safe_library_name,
    safe_library_path,
    sweep_orphan_parts,
    write_metadata,
)
from src.widgets.wad_finder.models import WadBrowserResult


def _result(**overrides) -> WadBrowserResult:
    base = dict(
        title="eviternity.wad",
        description="A megawad",
        metadata_text="boom | megawad",
        source_id="youfailit",
        source_name="Doomworld mirror",
        size_bytes=2048,
        remote_path="levels/doom2/eviternity.wad",
        download_url="https://example.com/idgames/levels/doom2/eviternity.wad",
        browser_url="https://example.com/idgames/levels/doom2/eviternity.wad",
    )
    base.update(overrides)
    return WadBrowserResult(**base)


class TestSafeLibraryName:
    def test_simple_name(self, tmp_path):
        assert safe_library_name("levels/doom2/a.wad", "src", tmp_path) == tmp_path / "a.wad"

    def test_empty_name_fallback(self, tmp_path):
        assert safe_library_name("", "src", tmp_path) == tmp_path / "mod_src.pkg"

    def test_collision_with_existing_file(self, tmp_path):
        (tmp_path / "a.wad").write_bytes(b"PWAD")
        assert safe_library_name("a.wad", "src", tmp_path) == tmp_path / "a_1__src.wad"

    def test_collision_with_reserved(self, tmp_path):
        reserved = {str(tmp_path / "a.wad").lower()}
        assert safe_library_name("a.wad", "src", tmp_path, reserved) == tmp_path / "a_1__src.wad"

    def test_multiple_collisions_increment(self, tmp_path):
        (tmp_path / "a.wad").write_bytes(b"PWAD")
        (tmp_path / "a_1__src.wad").write_bytes(b"PWAD")
        assert safe_library_name("a.wad", "src", tmp_path) == tmp_path / "a_2__src.wad"


class TestMetadataRoundTrip:
    def test_write_then_read(self, tmp_path):
        target = tmp_path / "a.wad"
        target.write_bytes(b"PWAD")
        result = _result()
        write_metadata(str(target), result)

        meta = read_metadata(str(target))
        assert meta["title"] == "eviternity.wad"
        assert meta["description"] == "A megawad"
        assert meta["metadata_text"] == "boom | megawad"
        assert meta["source_id"] == "youfailit"
        assert meta["source_name"] == "Doomworld mirror"
        assert meta["remote_path"] == "levels/doom2/eviternity.wad"
        assert meta["download_url"] == result.download_url
        assert meta["browser_url"] == result.browser_url
        assert meta["size_bytes"] == 2048
        assert isinstance(meta["downloaded_at"], int) and meta["downloaded_at"] > 0

    def test_metadata_path_suffix(self):
        assert metadata_path("/lib/a.wad").name == "a.wad.bfg-meta.json"

    def test_read_missing_returns_empty(self, tmp_path):
        assert read_metadata(str(tmp_path / "nope.wad")) == {}

    def test_read_corrupt_returns_empty(self, tmp_path):
        target = tmp_path / "b.wad"
        target.write_bytes(b"PWAD")
        metadata_path(str(target)).write_text("{not json", encoding="utf-8")
        assert read_metadata(str(target)) == {}

    def test_write_is_atomic_no_tmp_left(self, tmp_path):
        target = tmp_path / "a.wad"
        target.write_bytes(b"PWAD")
        write_metadata(str(target), _result())
        assert not list(tmp_path.glob("*.tmp"))
        assert metadata_path(str(target)).exists()


class TestCollectLibraryRows:
    def test_collects_mod_files_only(self, tmp_path):
        (tmp_path / "a.wad").write_bytes(b"PWADxxxx")
        (tmp_path / "b.pk3").write_bytes(b"PK")
        (tmp_path / "readme.txt").write_text("nope")
        rows = collect_library_rows(tmp_path)
        assert [r["name"] for r in rows] == ["a.wad", "b.pk3"]

    def test_row_fields_with_metadata(self, tmp_path):
        target = tmp_path / "a.wad"
        target.write_bytes(b"PWADxxxx")
        write_metadata(str(target), _result())
        rows = collect_library_rows(tmp_path)
        row = rows[0]
        assert row["title"] == "eviternity.wad"
        assert row["source"] == "Doomworld mirror"
        assert row["source_path"] == "levels/doom2/eviternity.wad"
        assert row["source_id"] == "youfailit"
        assert row["size"] == 8
        assert row["size_text"] == "8 B"
        assert row["installed_text"] != "local"  # downloaded_at was written

    def test_row_fields_without_metadata(self, tmp_path):
        (tmp_path / "plain.wad").write_bytes(b"PWAD")
        rows = collect_library_rows(tmp_path)
        row = rows[0]
        assert row["title"] == "plain.wad"
        assert row["source"] == "local"
        assert row["source_path"] == "-"
        assert row["installed_text"] == "local"

    def test_sorted_by_name(self, tmp_path):
        for name in ("zeta.wad", "Alpha.wad", "mid.pk3"):
            (tmp_path / name).write_bytes(b"x")
        rows = collect_library_rows(tmp_path)
        assert [r["name"] for r in rows] == ["Alpha.wad", "mid.pk3", "zeta.wad"]


class TestLibraryRowMatches:
    ROW = {
        "name": "eviternity.wad",
        "title": "Eviternity",
        "description": "A megawad",
        "metadata_text": "boom",
        "source": "Doomworld mirror",
        "source_path": "levels/doom2/eviternity.wad",
        "source_id": "youfailit",
    }

    def test_empty_query_matches(self):
        assert library_row_matches(self.ROW, "")
        assert library_row_matches(self.ROW, "   ")

    def test_all_tokens_must_match(self):
        assert library_row_matches(self.ROW, "megawad boom")
        assert not library_row_matches(self.ROW, "megawad missing")

    def test_matches_source_and_path(self):
        assert library_row_matches(self.ROW, "youfailit")
        assert library_row_matches(self.ROW, "doom2")


class TestIdentityMapAndSafePath:
    def test_identity_map_uses_metadata(self, tmp_path):
        target = tmp_path / "renamed.wad"
        target.write_bytes(b"PWAD")
        write_metadata(str(target), _result())
        identity_map = build_library_identity_map(tmp_path)
        assert identity_map[("levels/doom2/eviternity.wad", "youfailit")] == target

    def test_safe_library_path_reuses_existing_identity(self, tmp_path):
        target = tmp_path / "renamed.wad"
        target.write_bytes(b"PWAD")
        write_metadata(str(target), _result())
        result = _result()
        assert safe_library_path(result, tmp_path) == target

    def test_safe_library_path_falls_back_to_name(self, tmp_path):
        result = _result()
        assert safe_library_path(result, tmp_path) == tmp_path / "eviternity.wad"


class TestSweepOrphanParts:
    def test_removes_part_files_only(self, tmp_path):
        (tmp_path / "a.wad.1234.part").write_bytes(b"x")
        (tmp_path / "b.part").write_bytes(b"x")
        (tmp_path / "keep.wad").write_bytes(b"PWAD")
        sweep_orphan_parts(tmp_path)
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == ["keep.wad"]

    def test_missing_dir_is_ignored(self, tmp_path):
        sweep_orphan_parts(tmp_path / "does-not-exist")  # must not raise
