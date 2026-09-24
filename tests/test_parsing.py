"""Unit tests for src.widgets.wad_finder.parsing (pure, network-free)."""

import json

from src.widgets.wad_finder.parsing import (
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
from src.widgets.wad_finder.models import WadBrowserResult

SOURCE = {
    "id": "testsrc",
    "name": "Test Mirror",
    "base": "https://mirror.example.com/pub/idgames",
    "browser": "https://mirror.example.com/pub/idgames",
    "index": "fullsort.gz",
    "parser": "fullsort",
}


class TestDatetimeTokens:
    def test_date_formats(self):
        assert looks_like_datetime_token("2024-01-31")
        assert looks_like_datetime_token("2024/01/31")
        assert looks_like_datetime_token("20240131")
        assert looks_like_datetime_token("13:37")
        assert looks_like_datetime_token("13:37:59")

    def test_non_dates(self):
        assert not looks_like_datetime_token("")
        assert not looks_like_datetime_token("eviternity")
        assert not looks_like_datetime_token("12345")

    def test_calendar_validity_not_checked(self):
        # Legacy behavior: the shape regex accepts impossible dates.
        assert looks_like_datetime_token("2024-13-99")


class TestFullsortTokens:
    def test_date_size_path_desc(self):
        assert parse_fullsort_tokens(["2024-01-31", "12345", "levels/doom2/a.wad", "Cool", "map"]) == (
            12345,
            "levels/doom2/a.wad",
            "Cool map",
        )

    def test_size_path_desc(self):
        assert parse_fullsort_tokens(["2.5m", "mods/b.pk3", "Big mod"]) == (
            int(2.5 * 1024**2),
            "mods/b.pk3",
            "Big mod",
        )

    def test_date_time_size_path(self):
        assert parse_fullsort_tokens(["2024-01-31", "13:37", "10k", "c.zip"]) == (10 * 1024, "c.zip", "")

    def test_size_with_commas_and_units(self):
        assert parse_fullsort_tokens(["1,024", "d.wad"]) == (1024, "d.wad", "")
        assert parse_fullsort_tokens(["3g", "e.pk3"]) == (3 * 1024**3, "e.pk3", "")
        assert parse_fullsort_tokens(["128KiB", "f.wad"]) == (128 * 1024, "f.wad", "")

    def test_invalid(self):
        assert parse_fullsort_tokens([]) is None
        assert parse_fullsort_tokens(["just", "words"]) is None
        assert parse_fullsort_tokens(["12345"]) is None  # size but no path


class TestFullsort:
    SAMPLE = """\
fullpath of some header
Control switch header line
# comment line
; another comment

2024-01-31 12345 levels/doom2/a.wad Cool map
2.5m mods/b.pk3 Big mod
12345 notamod.txt Description here
bogus line without size or path
"""

    def test_parses_mod_files_only(self):
        entries = parse_fullsort(self.SAMPLE)
        assert entries == [
            (12345, "levels/doom2/a.wad", "Cool map"),
            (int(2.5 * 1024**2), "mods/b.pk3", "Big mod"),
        ]

    def test_empty(self):
        assert parse_fullsort("") == []


class TestParseText:
    TEXT = """\
# comment
levels/doom2/a.wad A cool map
2048 mods/b.pk3 With a size prefix
random text with eviternity.wad embedded
nothing useful here
"""

    def test_path_first(self):
        entries = parse_text(self.TEXT, SOURCE, "")
        remotes = [e[1] for e in entries]
        assert "levels/doom2/a.wad" in remotes
        assert "mods/b.pk3" in remotes
        assert "eviternity.wad" in remotes
        assert len(entries) == 3

    def test_size_prefix_parsed(self):
        entries = parse_text(self.TEXT, SOURCE, "")
        by_remote = {e[1]: e for e in entries}
        assert by_remote["mods/b.pk3"][0] == 2048
        assert by_remote["mods/b.pk3"][2] == "With a size prefix"

    def test_query_filter(self):
        entries = parse_text(self.TEXT, SOURCE, "cool")
        assert [e[1] for e in entries] == ["levels/doom2/a.wad"]

    def test_empty_base(self):
        assert parse_text(self.TEXT, {"base": "", "browser": ""}, "") == []


class TestIdgamesApi:
    def _payload(self):
        return json.dumps(
            {
                "content": {
                    "file": [
                        {
                            "id": 12345,
                            "title": "eviternity.wad",
                            "dir": "levels/doom2",
                            "size": "12,345",
                            "description": "A megawad",
                        },
                        {
                            "filename": "readme.txt",  # not a mod file
                            "path": "docs",
                        },
                        {
                            "filename": "bloodr.wad",
                            "path": "levels/doom2",
                            "size": 2048,
                            "author": "Nobody",
                        },
                    ]
                }
            }
        )

    def test_parses_files(self):
        results = parse_idgames_api(SOURCE, self._payload(), "", allow_empty_query=True)
        remotes = [r.remote_path for r in results]
        assert remotes == ["levels/doom2/eviternity.wad", "levels/doom2/bloodr.wad"]

    def test_fields(self):
        results = parse_idgames_api(SOURCE, self._payload(), "", allow_empty_query=True)
        first = results[0]
        assert first.title == "eviternity.wad"
        assert first.size_bytes == 12345
        assert first.description == "A megawad"
        assert first.source_id == "testsrc"
        assert first.download_url == "https://mirror.example.com/pub/idgames/levels/doom2/eviternity.wad"

    def test_author_becomes_description(self):
        results = parse_idgames_api(SOURCE, self._payload(), "", allow_empty_query=True)
        assert results[1].description == "Author: Nobody"

    def test_dir_trailing_slash_no_double_slash(self):
        # A trailing slash in "dir"/"path" must not produce a double slash.
        payload = json.dumps({"files": [{"filename": "a.wad", "path": "levels/doom2/"}]})
        results = parse_idgames_api(SOURCE, payload, "", allow_empty_query=True)
        assert results[0].remote_path == "levels/doom2/a.wad"

    def test_requires_query_unless_allowed(self):
        assert parse_idgames_api(SOURCE, self._payload(), "") == []

    def test_query_filter(self):
        results = parse_idgames_api(SOURCE, self._payload(), "blood")
        assert [r.title for r in results] == ["bloodr.wad"]

    def test_invalid_json(self):
        assert parse_idgames_api(SOURCE, "not json", "x") == []

    def test_fallback_payload_shape(self):
        payload = json.dumps({"path": "levels/doom2/odd.wad"})
        results = parse_idgames_api(SOURCE, payload, "", allow_empty_query=True)
        assert [r.remote_path for r in results] == ["levels/doom2/odd.wad"]


class TestCollectApiItems:
    def test_nested_containers(self):
        payload = {"content": {"file": [{"filename": "a.wad"}, {"filename": "b.wad"}]}}
        items = collect_api_items(payload)
        assert [i["filename"] for i in items] == ["a.wad", "b.wad"]

    def test_direct_dict(self):
        assert collect_api_items({"filename": "a.wad"}) == [{"filename": "a.wad"}]

    def test_empty(self):
        assert collect_api_items(None) == []
        assert collect_api_items({}) == []
        assert collect_api_items(42) == []


class TestCoerceIntAndApiPath:
    def test_coerce_int(self):
        assert coerce_int("12,345") == 12345
        assert coerce_int("2.9") == 2
        assert coerce_int(True) == 1
        assert coerce_int("junk") == 0
        assert coerce_int(None) == 0

    def test_normalize_api_path(self):
        assert normalize_api_path("levels\\doom2\\a.wad?dl=1#frag") == "levels/doom2/a.wad"
        assert normalize_api_path("") == ""
        assert normalize_api_path("/pub/idgames/a.wad") == "pub/idgames/a.wad"


class TestParseJson:
    def test_delegates_with_empty_query_allowed(self):
        payload = json.dumps({"files": [{"filename": "a.wad", "path": "x"}]})
        results = parse_json(SOURCE, payload, "")
        assert [r.remote_path for r in results] == ["x/a.wad"]


class TestParseRss:
    RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>mods</title>
    <item>
      <title>coolmap.wad</title>
      <link>https://mirror.example.com/pub/idgames/levels/doom2/coolmap.wad</link>
      <description>A cool map</description>
      <author>Mapper</author>
      <enclosure url="https://mirror.example.com/pub/idgames/levels/doom2/coolmap.wad" length="4096"/>
    </item>
    <item>
      <title>not-a-mod-page</title>
      <link>https://mirror.example.com/news/1</link>
    </item>
  </channel>
</rss>
"""

    ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>atommap.pk3</title>
    <link href="https://mirror.example.com/pub/idgames/mods/atommap.pk3"/>
    <summary>Atom entry</summary>
  </entry>
</feed>
"""

    def test_rss_items(self):
        results = parse_rss(SOURCE, self.RSS, "")
        assert len(results) == 1
        r = results[0]
        assert r.title == "coolmap.wad"
        assert r.remote_path == "pub/idgames/levels/doom2/coolmap.wad"
        assert r.size_bytes == 4096
        assert r.description == "A cool map"
        assert "Mapper" in r.metadata_text

    def test_query_filter(self):
        assert parse_rss(SOURCE, self.RSS, "zzqq") == []
        assert len(parse_rss(SOURCE, self.RSS, "coolmap")) == 1

    def test_single_letter_tokens_make_fuzzy_matches(self):
        # Legacy quirk: the standalone "a" in "A cool map" is a search token,
        # and "a" is a substring of "nomatch".
        assert len(parse_rss(SOURCE, self.RSS, "nomatch")) == 1

    def test_atom_href_only_link(self):
        # Atom feeds carry href-only <link href="..."/> elements; these used
        # to raise KeyError via the invalid "link/@href" ElementPath lookup.
        results = parse_rss(SOURCE, self.ATOM, "")
        assert len(results) == 1
        r = results[0]
        assert r.title == "atommap.pk3"
        assert r.remote_path == "pub/idgames/mods/atommap.pk3"
        assert r.download_url == "https://mirror.example.com/pub/idgames/mods/atommap.pk3"

    def test_invalid_xml(self):
        assert parse_rss(SOURCE, "<rss><broken", "") == []

    def test_billion_laughs_blocked(self):
        bomb = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title><link>x.wad</link></item></channel></rss>"""
        # defusedxml must reject entity-expansion payloads
        assert parse_rss(SOURCE, bomb, "") == []


class TestHtmlLinks:
    HTML = """
<html><body>
<a href="../">Parent Directory</a>
<a href="levels/doom2/a.wad">a.wad</a>  01-Jan-2024 12345
<a href="mods/">mods/</a>
<a href="https://mirror.example.com/pub/idgames/mods/b.pk3"><b>b.pk3</b> &amp; more</a>
<a href="#anchor">anchor</a>
<a href="javascript:void(0)">js</a>
<a href="mailto:x@y.z">mail</a>
<a href="readme.txt">readme.txt</a>
<a href="levels/doom2/a.wad">duplicate</a>
</body></html>
"""

    def test_iter_html_links(self):
        links = list(iter_html_links(self.HTML))
        hrefs = [h for h, _ in links]
        assert "levels/doom2/a.wad" in hrefs
        assert "#anchor" not in hrefs
        assert not any(h.startswith(("javascript:", "mailto:")) for h in hrefs)
        labels = dict(links)
        assert labels["https://mirror.example.com/pub/idgames/mods/b.pk3"] == "b.pk3 & more"

    def test_link_targets(self):
        assert list(link_targets('<a href="x.wad">x</a>')) == ["x.wad"]

    def test_parse_html(self):
        found = parse_html(SOURCE, self.HTML, "")
        # relative_path_for strips the base path, so remotes are relative to
        # the source base (no "pub/idgames" prefix).
        remotes = [r[1] for r in found]
        assert "levels/doom2/a.wad" in remotes
        assert "mods/b.pk3" in remotes
        assert not any(r.endswith("readme.txt") for r in remotes)
        # duplicates and directory links removed
        assert remotes.count("levels/doom2/a.wad") == 1
        assert not any(r.endswith("/") for r in remotes)

    def test_parse_html_query(self):
        # ("b.pk3" would also match a.wad via the single-char token "b" in
        # "pub" — legacy fuzzy behavior; use a distinctive token.)
        found = parse_html(SOURCE, self.HTML, "pk3")
        assert [r[1] for r in found] == ["mods/b.pk3"]

    def test_relative_path_for(self):
        base = "https://mirror.example.com/pub/idgames"
        # The base path is stripped from same-host candidates.
        assert relative_path_for(base, f"{base}/levels/a.wad") == "levels/a.wad"
        assert relative_path_for("https://mirror.example.com", "https://mirror.example.com/x/a.wad") == "x/a.wad"
        assert relative_path_for(base, "https://other.example.com/pub/idgames/a.wad") == ""
        # Same-URL candidate: fully consumed by the base path.
        assert relative_path_for(base, base) == ""


class TestCrawlHtml:
    # Use a host-only base so relative paths are the full URL path.
    CRAWL_SOURCE = {**SOURCE, "base": "https://mirror.example.com", "browser": "https://mirror.example.com"}
    PAGES = {
        "https://mirror.example.com/": '<a href="levels/">levels/</a><a href="root.wad">root.wad</a>',
        "https://mirror.example.com/levels/": '<a href="a.wad">a.wad</a><a href="b.txt">b.txt</a>',
    }

    def _fetch(self, url: str) -> str:
        if url not in self.PAGES:
            raise RuntimeError(f"404 {url}")
        return self.PAGES[url]

    def test_crawl_finds_files_across_dirs(self):
        results = crawl_html(self.CRAWL_SOURCE, "", "", fetch=self._fetch)
        remotes = sorted(r[1] for r in results)
        assert remotes == ["levels/a.wad", "root.wad"]

    def test_crawl_with_query(self):
        results = crawl_html(self.CRAWL_SOURCE, "", "root", fetch=self._fetch)
        assert [r[1] for r in results] == ["root.wad"]

    def test_crawl_start_path(self):
        assert crawl_start_path("fullsort.gz") == ""
        assert crawl_start_path("api.php") == ""
        assert crawl_start_path("list.txt") == ""
        assert crawl_start_path("levels") == "levels"
        assert crawl_start_path("") == ""


class TestTextfileMetadata:
    SAMPLE = """\
===========================================================================
Title                           : Eviternity
Filename                        : eviternity.wad
Release date                    : 12/10/2018
Author                          : Dragonfly & co
Description                     : A 32-map megawad
                                  for Doom II.

                                  Boom compatible.
===========================================================================
* Reviews *
"""

    def test_fields_extracted(self):
        fields = parse_textfile_metadata(self.SAMPLE)
        assert fields["title"] == "Eviternity"
        assert fields["filename"] == "eviternity.wad"
        assert fields["author"] == "Dragonfly & co"
        assert "A 32-map megawad" in fields["description"]
        assert "Boom compatible." in fields["description"]

    def test_empty(self):
        assert parse_textfile_metadata("") == {}

    def test_non_matching_text(self):
        assert parse_textfile_metadata("just some random prose\nnothing structured") == {}


class TestCandidateTextfileUrls:
    def _result(self, **kw):
        base = dict(
            title="a.wad",
            description="",
            metadata_text="",
            source_id="s",
            source_name="S",
            size_bytes=0,
            remote_path="levels/doom2/a.wad",
            download_url="",
            browser_url="",
        )
        base.update(kw)
        return WadBrowserResult(**base)

    def test_primary_txt_candidate(self):
        urls = candidate_textfile_urls(SOURCE, self._result())
        assert urls[0] == "https://mirror.example.com/pub/idgames/levels/doom2/a.txt"

    def test_zip_fallback(self):
        urls = candidate_textfile_urls(SOURCE, self._result(remote_path="mods/b.zip"))
        assert "https://mirror.example.com/pub/idgames/mods/b.txt" in urls

    def test_hash_prefixed_name(self):
        urls = candidate_textfile_urls(SOURCE, self._result(remote_path="levels/#c.wad"))
        assert "https://mirror.example.com/pub/idgames/levels/c.txt" in urls

    def test_empty_base_or_remote(self):
        assert candidate_textfile_urls({"base": ""}, self._result()) == []
        assert candidate_textfile_urls(SOURCE, self._result(remote_path="")) == []

    def test_deduped(self):
        urls = candidate_textfile_urls(SOURCE, self._result())
        assert len(urls) == len(set(urls))


class TestEnrichmentHelpers:
    def _result(self, title, remote, size=0, description="", metadata_text=""):
        return WadBrowserResult(
            title=title,
            description=description,
            metadata_text=metadata_text,
            source_id="s",
            source_name="S",
            size_bytes=size,
            remote_path=remote,
            download_url="",
            browser_url="",
        )

    def test_candidates_prefer_query_hits(self):
        results = [
            self._result("nomatch.wad", "a/nomatch.wad", size=10),
            self._result("blood.wad", "b/blood.wad", size=5),
        ]
        candidates = textfile_enrichment_candidates("blood river", results)
        assert candidates[0].title == "blood.wad"

    def test_candidates_fall_back_to_all_results(self):
        results = [
            self._result("small.wad", "a/small.wad", size=10),
            self._result("big.wad", "b/big.wad", size=999),
        ]
        candidates = textfile_enrichment_candidates("zzz qqq", results)
        # No token hits anywhere -> every result is a candidate, ordered by the
        # deterministic tie-break key (score, remote_path, size, title).
        assert [c.title for c in candidates] == ["small.wad", "big.wad"]

    def test_apply_textfile_metadata(self):
        item = self._result("a.wad", "a/a.wad", description="Old")
        applied = apply_textfile_metadata(item, {"description": "New desc", "author": "Mapper"})
        assert applied is True
        assert "Old" in item.description and "New desc" in item.description
        assert "Mapper" in item.metadata_text
        assert "New desc" in item.metadata_text

    def test_apply_textfile_metadata_empty(self):
        item = self._result("a.wad", "a/a.wad")
        assert apply_textfile_metadata(item, {}) is False

    def test_apply_textfile_metadata_truncates(self):
        item = self._result("a.wad", "a/a.wad", metadata_text="x" * 1700)
        apply_textfile_metadata(item, {"author": "y" * 500})
        assert len(item.metadata_text) <= 1800
