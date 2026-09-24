"""Unit tests for src.widgets.wad_finder.sources and models.Source (Qt-free)."""

from src.widgets.wad_finder.constants import DEFAULT_SOURCES, SOURCE_STATUS_UNKNOWN
from src.widgets.wad_finder.models import Source, SourceStatus
from src.widgets.wad_finder.sources import (
    allocate_source_id,
    coerce_source,
    custom_source_id,
    default_index_for_parser,
    infer_source_parser,
    is_idgames_source,
    make_host_name,
    normalize_parser,
    normalize_source_id,
    normalize_source_url,
    parse_custom_source_entry,
    seed_sources,
)


class TestNormalizeParser:
    def test_aliases(self):
        assert normalize_parser("API") == "idgames_api"
        assert normalize_parser("id-api") == "idgames_api"
        assert normalize_parser("idgamesapi") == "idgames_api"
        assert normalize_parser("crawler") == "html"
        assert normalize_parser("web_crawl") == "html"
        assert normalize_parser("js") == "json"
        assert normalize_parser("jsn") == "json"
        assert normalize_parser("atom") == "rss"
        assert normalize_parser("xml") == "rss"
        assert normalize_parser("txt") == "text"

    def test_canonical_names_passthrough(self):
        for name in ("fullsort", "html", "auto", "idgames_api", "json", "rss", "text"):
            assert normalize_parser(name) == name

    def test_unknown_and_empty_fall_back_to_default(self):
        assert normalize_parser("") == "fullsort"
        assert normalize_parser("bogus") == "fullsort"

    def test_whitespace_and_dash_normalization(self):
        assert normalize_parser(" IDGAMES-API ") == "idgames_api"


class TestCoerceSource:
    def test_defaults(self):
        out = coerce_source({})
        assert out == {
            "id": "source",
            "name": "source",
            "base": "",
            "index": "fullsort.gz",
            "browser": "",
            "parser": "fullsort",
            "enabled": True,
            "status": "unknown",
            "status_message": "",
            "status_checked_at": 0.0,
        }

    def test_invalid_status_falls_back_to_unknown(self):
        out = coerce_source({"id": "x", "status": "BOGUS"})
        assert out["status"] == SOURCE_STATUS_UNKNOWN

    def test_valid_status_preserved(self):
        out = coerce_source({"id": "x", "status": "OK"})
        assert out["status"] == "ok"

    def test_scheme_added_and_trailing_slash_stripped(self):
        out = coerce_source({"id": " a b! ", "base": "example.com/idgames/", "parser": "API"})
        assert out["id"] == "a_b"
        assert out["base"] == "https://example.com/idgames"
        assert out["browser"] == "https://example.com/idgames"
        assert out["parser"] == "idgames_api"

    def test_name_defaults_to_id(self):
        out = coerce_source({"id": "x", "name": "  "})
        assert out["name"] == "x"


class TestSeedSources:
    def test_defaults_when_no_state(self):
        seeded = seed_sources(None)
        assert len(seeded) == len(DEFAULT_SOURCES)
        assert all(s["base"] for s in seeded)

    def test_state_overrides_and_missing_defaults_appended(self):
        state = [{"id": "custom1", "base": "https://custom.example.com/idgames"}]
        seeded = seed_sources(state)
        ids = [s["id"] for s in seeded]
        assert ids[0] == "custom1"
        # missing built-ins are appended
        assert "youfailit" in ids

    def test_duplicate_ids_and_empty_base_skipped(self):
        state = [
            {"id": "dup", "base": "https://a.example.com"},
            {"id": "dup", "base": "https://b.example.com"},
            {"id": "nobase", "base": ""},
        ]
        seeded = seed_sources(state, default_sources=[])
        assert [s["id"] for s in seeded] == ["dup"]
        assert seeded[0]["base"] == "https://a.example.com"

    def test_non_dict_entries_ignored(self):
        seeded = seed_sources(["nope", {"id": "ok", "base": "https://x.example.com"}], default_sources=[])
        assert [s["id"] for s in seeded] == ["ok"]


class TestNormalizeSourceId:
    def test_sanitizes(self):
        assert normalize_source_id(" a b! ") == "a_b"
        assert normalize_source_id("youfailit") == "youfailit"
        assert normalize_source_id("https://x.com/a") == "https___x_com_a"

    def test_strips_underscores(self):
        assert normalize_source_id("__x__") == "x"


class TestIsIdgamesSource:
    def test_base_or_browser(self):
        assert is_idgames_source({"base": "https://x.com/pub/idgames"})
        assert is_idgames_source({"base": "", "browser": "https://x.com/Idgames"})
        assert not is_idgames_source({"base": "https://x.com/files", "browser": ""})


class TestNormalizeSourceUrl:
    def test_adds_scheme(self):
        assert normalize_source_url("example.com/idgames") == "https://example.com/idgames"

    def test_scheme_relative(self):
        assert normalize_source_url("//example.com/idgames") == "https://example.com/idgames"

    def test_existing_scheme_kept(self):
        assert normalize_source_url("http://example.com/x") == "http://example.com/x"

    def test_empty(self):
        assert normalize_source_url("") == ""


class TestMakeHostName:
    def test_netloc_with_path(self):
        assert make_host_name("https://example.com/pub/idgames") == "example.com (/pub/idgames)"

    def test_netloc_only(self):
        assert make_host_name("https://example.com") == "example.com"

    def test_no_netloc(self):
        assert make_host_name("not a url") == "not a url"


class TestInferSourceParser:
    def test_api(self):
        assert infer_source_parser("https://x.com/api/api.php", "") == "idgames_api"
        assert infer_source_parser("https://x.com/base", "api.php") == "idgames_api"

    def test_rss(self):
        assert infer_source_parser("https://x.com/rss/feed", "") == "rss"

    def test_json(self):
        assert infer_source_parser("https://x.com/index.json", "") == "json"
        assert infer_source_parser("https://x.com/base", "index.json.gz") == "json"

    def test_text(self):
        assert infer_source_parser("https://x.com/list.txt", "") == "text"

    def test_doomworld_idgames_is_auto(self):
        assert infer_source_parser("https://www.doomworld.com/idgames/", "") == "auto"

    def test_gzip_is_fullsort(self):
        assert infer_source_parser("https://x.com/dl.gz", "") == "fullsort"

    def test_directory_layout_is_html(self):
        assert infer_source_parser("https://x.com/pub/idgames", "") == "html"
        assert infer_source_parser("https://x.com/files/", "") == "html"

    def test_default(self):
        assert infer_source_parser("https://x.com", "") == "fullsort"
        assert infer_source_parser("", "") == "fullsort"


class TestDefaultIndexForParser:
    def test_explicit_index_kept(self):
        assert default_index_for_parser("json", "custom.json") == "custom.json"

    def test_defaults(self):
        assert default_index_for_parser("idgames_api", "") == "api.php"
        assert default_index_for_parser("json", "") == "index.json"
        assert default_index_for_parser("rss", "") == "rss.xml"
        assert default_index_for_parser("text", "") == "index.txt"
        assert default_index_for_parser("html", "") == ""
        assert default_index_for_parser("fullsort", "") == "fullsort.gz"
        assert default_index_for_parser("auto", "") == "fullsort.gz"


class TestParseCustomSourceEntry:
    def test_url_only(self):
        out = parse_custom_source_entry("https://example.com/pub/idgames")
        assert out == {
            "name": "example.com (/pub/idgames)",
            "url": "https://example.com/pub/idgames",
            "index": "fullsort.gz",
            "parser": "html",
            "base": "https://example.com/pub/idgames",
        }

    def test_pipe_separated_url_first(self):
        out = parse_custom_source_entry("https://example.com/pub/idgames | fullsort.gz | fullsort")
        assert out["url"] == "https://example.com/pub/idgames"
        assert out["index"] == "fullsort.gz"
        assert out["parser"] == "fullsort"

    def test_name_first(self):
        out = parse_custom_source_entry("MyMirror, https://example.com/files")
        assert out["name"] == "MyMirror"
        assert out["url"] == "https://example.com/files"
        assert out["parser"] == "html"

    def test_key_value_form(self):
        out = parse_custom_source_entry("url=https://foo.bar/idgames | name=Foo | parser=crawl")
        assert out == {
            "name": "Foo",
            "url": "https://foo.bar/idgames",
            "index": "",
            "parser": "html",
            "base": "https://foo.bar/idgames",
        }

    def test_json_form(self):
        out = parse_custom_source_entry('{"url": "https://json.example.com/index.json", "name": "J", "parser": "jsn"}')
        assert out == {
            "name": "J",
            "url": "https://json.example.com/index.json",
            "index": "index.json",
            "parser": "json",
            "base": "https://json.example.com/index.json",
        }

    def test_scheme_relative_url(self):
        out = parse_custom_source_entry("//schemeless.example.com/idgames")
        assert out["url"] == "https://schemeless.example.com/idgames"

    def test_comments_and_empty_rejected(self):
        assert parse_custom_source_entry("# comment") is None
        assert parse_custom_source_entry("") is None
        assert parse_custom_source_entry("   ") is None

    def test_parser_inference_from_url(self):
        out = parse_custom_source_entry("https://example.com/feed.rss")
        assert out["parser"] == "rss"


class TestCustomSourceIdAndAllocation:
    def test_custom_source_id(self):
        assert custom_source_id("https://example.com/idgames", None) == "custom:https___example_com_idgames"
        assert custom_source_id("", "My Name") == "custom:My_Name"

    def test_allocate_source_id_dedupes(self):
        existing = {"source", "source-2"}
        assert allocate_source_id("source", existing) == "source-3"
        assert allocate_source_id("new", existing) == "new"
        assert allocate_source_id("!!!", existing) == "source-3"  # normalizes to "source"


class TestSourceModel:
    SAMPLE = {
        "id": "youfailit",
        "name": "Doomworld / idgames mirror (youfailit)",
        "base": "https://youfailit.net/pub/idgames",
        "index": "fullsort.gz",
        "browser": "https://youfailit.net/pub/idgames",
        "parser": "fullsort",
        "enabled": True,
        "status": "ok",
        "status_message": "3 result(s)",
        "status_checked_at": 123.5,
    }

    def test_round_trip_byte_compatible(self):
        source = Source.from_dict(self.SAMPLE)
        assert source.to_dict() == self.SAMPLE

    def test_from_dict_normalizes_like_coerce_source(self):
        source = Source.from_dict({"base": "example.com/idgames/", "status": "junk"})
        assert source.base == "https://example.com/idgames"
        assert source.status == "unknown"

    def test_key_order_matches_config_shape(self):
        assert list(Source.from_dict(self.SAMPLE).to_dict().keys()) == [
            "id",
            "name",
            "base",
            "index",
            "browser",
            "parser",
            "enabled",
            "status",
            "status_message",
            "status_checked_at",
        ]


class TestSourceStatus:
    def test_string_values(self):
        assert [s.value for s in SourceStatus] == [
            "unknown",
            "checking",
            "cached",
            "ok",
            "unreachable",
            "error",
            "disabled",
        ]

    def test_str_enum_compares_equal_to_plain_strings(self):
        assert SourceStatus.OK == "ok"
        assert SourceStatus.DISABLED.value == "disabled"
