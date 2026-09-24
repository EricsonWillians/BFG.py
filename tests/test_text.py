"""Unit tests for src.widgets.wad_finder.text (pure, Qt-free)."""

from src.widgets.wad_finder.models import WadBrowserResult
from src.widgets.wad_finder.text import (
    human_size,
    looks_like_mod_file,
    metadata_from_mapping,
    normalize_metadata_text,
    normalize_remote_path,
    query_matches,
    query_matches_any,
    result_identity,
    result_search_blob,
    search_tokens_from_text,
    short_age,
    summarize_text,
    tokenize_query,
)


def _result(**overrides) -> WadBrowserResult:
    base = dict(
        title="eviternity.wad",
        description="A megawad",
        metadata_text="",
        source_id="src1",
        source_name="Mirror",
        size_bytes=1024,
        remote_path="levels/doom2/eviternity.wad",
        download_url="https://example.com/idgames/levels/doom2/eviternity.wad",
        browser_url="https://example.com/idgames/levels/doom2/eviternity.wad",
    )
    base.update(overrides)
    return WadBrowserResult(**base)


class TestTokenizeQuery:
    def test_basic(self):
        assert tokenize_query("The quick BROWN fox, with doom2!") == ["quick", "brown", "fox", "doom2"]

    def test_stop_words_removed(self):
        assert tokenize_query("a an and the of to with") == []

    def test_empty(self):
        assert tokenize_query("") == []
        assert tokenize_query(None) == []


class TestQueryMatching:
    def test_empty_query_matches_everything(self):
        assert query_matches("anything", "")
        assert query_matches_any("anything", "  ")

    def test_substring(self):
        assert query_matches("the eviternity megawad", "eviternity")

    def test_all_tokens_required_for_query_matches(self):
        assert query_matches("blood river map", "blood river")
        assert not query_matches("blood map", "blood river")

    def test_any_token_sufficient_for_query_matches_any(self):
        assert query_matches_any("blood map", "blood river")
        assert not query_matches_any("stone map", "blood river")

    def test_stop_word_only_query_fails(self):
        # no substring hit, and every query token is a stop word -> no match
        assert not query_matches("stone wood", "the and")
        assert not query_matches_any("stone wood", "the and")

    def test_alphanumeric_chunk_matching(self):
        # "blood" should match mixed filenames like "bloodr2.wad"
        assert query_matches("bloodr2.wad", "blood")

    def test_search_tokens_from_text_splits_alnum(self):
        tokens = search_tokens_from_text("doom2-mega.wad")
        assert "doom2" in tokens
        assert "doom" in tokens
        assert "mega" in tokens
        assert "wad" in tokens


class TestNormalizeMetadataText:
    def test_dedupes_case_insensitively_and_joins(self):
        assert normalize_metadata_text("Alpha", "alpha", "beta") == "Alpha | beta"

    def test_collapses_whitespace(self):
        assert normalize_metadata_text("a\n  b\tc") == "a b c"

    def test_flattens_iterables_and_skips_none(self):
        assert normalize_metadata_text(None, ["x", "y"], ("x",), "z") == "x | y | z"

    def test_empty(self):
        assert normalize_metadata_text() == ""
        assert normalize_metadata_text("", None, []) == ""


class TestResultIdentity:
    def test_prefers_remote_path(self):
        r = _result()
        assert result_identity(r) == "remote:levels/doom2/eviternity.wad"

    def test_falls_back_to_url_path(self):
        r = _result(remote_path="")
        assert result_identity(r) == "url:idgames/levels/doom2/eviternity.wad"

    def test_falls_back_to_title(self):
        r = _result(remote_path="", download_url="")
        assert result_identity(r) == "name:eviternity.wad"

    def test_case_insensitive(self):
        a = _result(remote_path="Levels/DOOM2/Eviternity.WAD")
        b = _result(remote_path="levels/doom2/eviternity.wad")
        assert result_identity(a) == result_identity(b)


class TestNormalizeRemotePath:
    def test_backslashes_and_slashes(self):
        assert normalize_remote_path("\\a\\b c.wad") == "a/b c.wad"

    def test_unquotes_percent_encoding(self):
        assert normalize_remote_path("/x/y%20z.pk3") == "x/y z.pk3"

    def test_empty(self):
        assert normalize_remote_path("") == ""
        assert normalize_remote_path("   ") == ""


class TestLooksLikeModFile:
    def test_known_extensions(self):
        for ext in (".wad", ".pk3", ".ipk3", ".pk7", ".pke", ".zip"):
            assert looks_like_mod_file(f"mod{ext}")
            assert looks_like_mod_file(f"mod{ext.upper()}")

    def test_rejects_others(self):
        assert not looks_like_mod_file("readme.txt")
        assert not looks_like_mod_file("mod.wad.bak")


class TestHumanSize:
    def test_zero_and_negative(self):
        assert human_size(0) == "—"
        assert human_size(-5) == "—"

    def test_units(self):
        assert human_size(512) == "512 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(1536) == "1.5 KB"
        assert human_size(5 * 1024**3) == "5.0 GB"
        assert human_size(2 * 1024**4) == "2.0 TB"


class TestMetadataFromMapping:
    def test_preferred_keys_in_order(self):
        payload = {"path": "levels/doom2", "title": "T", "author": "A", "ignored_key": "nope"}
        assert metadata_from_mapping(payload) == "T | A | levels/doom2"

    def test_list_values_flattened(self):
        payload = {"tags": ["boom", "mbf"], "genre": "slaughter"}
        assert metadata_from_mapping(payload) == "slaughter | boom | mbf"

    def test_non_scalar_values_skipped(self):
        payload = {"title": {"nested": 1}, "name": "ok"}
        assert metadata_from_mapping(payload) == "ok"


class TestResultSearchBlob:
    def test_includes_all_fields_lowercased(self):
        blob = result_search_blob(_result())
        assert "eviternity.wad" in blob
        assert "a megawad" in blob
        assert "mirror" in blob
        assert blob == blob.lower()


class TestSummarizeAndAge:
    def test_summarize_empty(self):
        assert summarize_text("") == "No description available."

    def test_summarize_short(self):
        assert summarize_text("hello world") == "hello world"

    def test_summarize_clips_at_word_boundary(self):
        text = "word " * 60
        out = summarize_text(text, limit=120)
        assert out.endswith("...")
        assert len(out) <= 120

    def test_short_age(self):
        assert short_age(0) == "just now"
        assert short_age(30) == "30s ago"
        assert short_age(300) == "5m ago"
        assert short_age(7200) == "2h ago"
        assert short_age(3 * 86400) == "3d ago"
