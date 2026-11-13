"""The MCP server, end to end through fastmcp's in-memory client, with fake sources."""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastmcp import Client

from ukdatapy import server

CSV = b"name,year,value\nA,2024,1\nB,2024,3\nC,2025,2\n"


def fake_source(name, *, hits=None, datasets=None, fail=None):
    def search(query, limit=10):
        if fail:
            raise fail
        return (hits or [])[:limit]

    def describe(dataset_id):
        if dataset_id not in (datasets or {}):
            raise LookupError(f"No dataset {dataset_id!r}")
        return datasets[dataset_id]

    return SimpleNamespace(SOURCE=name, search=search, describe=describe)


def dataset(source, dataset_id, resources):
    return {"source": source, "id": dataset_id, "title": "T", "description": "", "publisher": "", "updated": "",
            "url": "", "resources": resources}


@pytest.fixture
def sources(monkeypatch):
    fakes = {
        "ons": fake_source("ons", hits=[{"source": "ons", "id": "/a", "title": "ONS hit"}]),
        "london": fake_source("london", hits=[{"source": "london", "id": "x1", "title": "London hit"}],
                              datasets={"x1": dataset("london", "x1", [
                                  {"id": "r1", "name": "data", "format": "CSV", "url": "https://data.london.gov.uk/f.csv"},
                                  {"id": "evil", "name": "bad", "format": "CSV", "url": "https://evil.example.com/f.csv"},
                                  {"id": "zip", "name": "z", "format": "ZIP", "url": "https://data.london.gov.uk/f.zip"},
                              ])}),
        "datagovuk": fake_source("datagovuk", fail=RuntimeError("CKAN is down")),
    }
    monkeypatch.setattr(server, "SOURCES", fakes)
    return fakes


def call(tool, args):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(run())


def test_lists_the_six_tools():
    async def run():
        async with Client(server.mcp) as client:
            return sorted(t.name for t in await client.list_tools())
    assert asyncio.run(run()) == ["describe_dataset", "get_series", "preview_data", "read_page", "search_datasets",
                                  "search_policy"]


def test_search_fans_out_and_one_failing_source_does_not_hide_the_others(sources):
    out = call("search_datasets", {"query": "x"}).data
    assert [h["title"] for h in out["results"]] == ["ONS hit", "London hit"]
    assert out["errors"] == {"datagovuk": "CKAN is down"}


def test_search_can_be_limited_to_named_sources(sources):
    out = call("search_datasets", {"query": "x", "sources": ["london"]}).data
    assert [h["source"] for h in out["results"]] == ["london"]


def test_unknown_source_is_an_error(sources):
    result = call("describe_dataset", {"source": "nasa", "dataset_id": "x"})
    assert result.is_error and "'ons', 'london', 'datagovuk', 'govuk' or 'tfl'" in result.content[0].text


def test_describe_unknown_dataset_is_an_error(sources):
    result = call("describe_dataset", {"source": "london", "dataset_id": "nope"})
    assert result.is_error and "No dataset 'nope'" in result.content[0].text


def test_preview_downloads_the_catalogue_url_and_filters(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    out = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1",
                                "where": {"year": "2024"}, "sort_by": "value", "descending": True}).data
    assert [r["name"] for r in out["rows"]] == ["B", "A"]
    assert out["resource"]["url"] == "https://data.london.gov.uk/f.csv"


def test_preview_is_cached_for_the_day(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    assert len(fake_http.calls) == 1


def test_preview_refuses_a_resource_off_the_allowlist(sources, fake_http):
    result = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "evil"})
    assert result.is_error and "allowlist" in result.content[0].text
    assert fake_http.calls == []


def test_preview_unknown_resource_lists_the_ids(sources):
    result = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r9"})
    assert result.is_error and "r1" in result.content[0].text


PAGE = "https://www.ethnicity-facts-figures.service.gov.uk/household-income/latest/"
PAGE_HTML = (b'<a href="downloads/by-ethnicity.csv">By ethnicity</a>'
             b'<a href="https://assets.publishing.service.gov.uk/media/1/by-ethnicity.csv">Same name, other folder</a>'
             b'<a href="downloads/report.pdf">Report</a>')


@pytest.fixture
def webpage_dataset(fake_http):
    # The real datagovuk source: CKAN lists only a webpage resource, as for the ethnicity datasets.
    fake_http.add("https://ckan.publishing.service.gov.uk/api/3/action/package_show", params={"id": "hh"},
                  json={"success": True, "result": {"id": "pkg-hh", "name": "hh", "resources": [
                      {"id": "r1", "name": "Page", "format": "webpage", "url": PAGE},
                      {"id": "r2", "name": "Off list", "format": "HTML", "url": "https://evil.example.com/p"}]}})
    fake_http.add(PAGE, content=PAGE_HTML)
    fake_http.add(PAGE + "downloads/by-ethnicity.csv", content=CSV)
    return fake_http


def test_describe_does_not_open_webpages(webpage_dataset):
    out = call("describe_dataset", {"source": "datagovuk", "dataset_id": "hh"}).data
    assert [r["id"] for r in out["resources"]] == ["r1", "r2"]
    assert [c["url"] for c in webpage_dataset.calls if c["url"] == PAGE] == []


def test_preview_of_a_webpage_lists_its_files_by_file_name(webpage_dataset):
    result = call("preview_data", {"source": "datagovuk", "dataset_id": "hh", "resource_id": "r1"})
    assert not result.is_error, result.content[0].text
    out = result.data
    assert "rows" not in out
    assert [(f["id"], f["name"], f["format"]) for f in out["files"]] == [
        ("r1/by-ethnicity.csv", "By ethnicity", "CSV"),
        ("r1/by-ethnicity.csv-2", "Same name, other folder", "CSV"),
    ]


def test_preview_of_a_file_found_on_a_webpage(webpage_dataset):
    result = call("preview_data", {"source": "datagovuk", "dataset_id": "hh", "resource_id": "r1/by-ethnicity.csv"})
    assert not result.is_error, result.content[0].text
    out = result.data
    assert out["resource"]["found_on"] == "r1"
    assert out["resource"]["url"] == PAGE + "downloads/by-ethnicity.csv"
    assert [r["name"] for r in out["rows"]] == ["A", "B", "C"]


def test_unknown_file_on_a_webpage_lists_the_page_files(webpage_dataset):
    result = call("preview_data", {"source": "datagovuk", "dataset_id": "hh", "resource_id": "r1/nope.csv"})
    assert result.is_error and "r1/by-ethnicity.csv" in result.content[0].text


def test_a_webpage_off_the_allowlist_is_never_fetched(webpage_dataset):
    result = call("preview_data", {"source": "datagovuk", "dataset_id": "hh", "resource_id": "r2"})
    assert result.is_error and "allowlist" in result.content[0].text
    assert [c["url"] for c in webpage_dataset.calls if "evil" in c["url"]] == []


def test_preview_of_an_unsupported_format_is_an_error(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.zip", content=b"PK\x03\x04junk")
    result = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "zip"})
    assert result.is_error and "ZIP" in result.content[0].text


SERIES = {"title": "CPI annual rate", "cdid": "D7G7", "unit": "%", "release_date": "2026-10-21",
          "next_release": "", "dataset": "MM23", "url": "https://www.ons.gov.uk/x",
          "observations": [
              {"period": "2024", "value": 2.5, "frequency": "year"},
              {"period": "2025", "value": 3.4, "frequency": "year"},
              {"period": "2025 Q4", "value": 3.5, "frequency": "quarter"},
              {"period": "2025 NOV", "value": 3.2, "frequency": "month"},
              {"period": "2025 DEC", "value": 3.4, "frequency": "month"},
              {"period": "2026 JAN", "value": 3.0, "frequency": "month"},
          ]}


@pytest.fixture
def ons_series(monkeypatch, sources):
    sources["ons"].get_series = lambda series: SERIES


def test_series_defaults_to_monthly(ons_series):
    out = call("get_series", {"series": "/x"}).data
    assert [o["period"] for o in out["observations"]] == ["2025 NOV", "2025 DEC", "2026 JAN"]
    assert out["frequency"] == "month" and out["cdid"] == "D7G7"


def test_series_frequency_and_year_range(ons_series):
    out = call("get_series", {"series": "/x", "frequency": "month", "start_year": 2026}).data
    assert [o["period"] for o in out["observations"]] == ["2026 JAN"]
    out = call("get_series", {"series": "/x", "frequency": "year", "end_year": 2024}).data
    assert [o["value"] for o in out["observations"]] == [2.5]


def test_series_limit_keeps_the_latest(ons_series):
    out = call("get_series", {"series": "/x", "limit": 2}).data
    assert [o["period"] for o in out["observations"]] == ["2025 DEC", "2026 JAN"]
    assert out["truncated"] is True


GOVUK_PAGE = "/government/statistical-data-sets/example-tables"


@pytest.fixture
def govuk_page(fake_http):
    # The real govuk source over a Content API answer with one CSV and one file off the allowlist.
    fake_http.add("https://www.gov.uk/api/content" + GOVUK_PAGE, json={
        "base_path": GOVUK_PAGE, "title": "Example tables", "description": "", "public_updated_at": "2026-08-27T09:30:04+01:00",
        "links": {"organisations": [{"title": "Home Office"}]},
        "details": {"attachments": [
            {"attachment_type": "file", "filename": "tables.csv", "title": "Tables",
             "url": "https://assets.publishing.service.gov.uk/media/1/tables.csv"},
            {"attachment_type": "external", "title": "Elsewhere", "url": "https://evil.example.com/tables.csv"}]}})
    fake_http.add("https://assets.publishing.service.gov.uk/media/1/tables.csv", content=CSV)
    return fake_http


def test_govuk_is_a_source(govuk_page):
    out = call("preview_data", {"source": "govuk", "dataset_id": GOVUK_PAGE, "resource_id": "tables.csv",
                                "where": {"year": "2025"}}).data
    assert [r["name"] for r in out["rows"]] == ["C"]


def test_a_govuk_attachment_off_the_allowlist_is_never_fetched(govuk_page):
    ids = [r["id"] for r in call("describe_dataset", {"source": "govuk", "dataset_id": GOVUK_PAGE}).data["resources"]]
    result = call("preview_data", {"source": "govuk", "dataset_id": GOVUK_PAGE, "resource_id": ids[1]})
    assert result.is_error and "allowlist" in result.content[0].text
    assert not [c for c in govuk_page.calls if "evil.example.com" in c["url"]]


def test_a_govuk_external_page_lists_its_files_instead_of_reading_the_page_as_rows(fake_http):
    # ORR links its station usage release to a data portal page, not a file (seen on 2026-11-12).
    portal = "https://dataportal.orr.gov.uk/statistics/usage/estimates-of-station-usage"
    fake_http.add("https://www.gov.uk/api/content" + GOVUK_PAGE, json={
        "base_path": GOVUK_PAGE, "title": "Station usage", "public_updated_at": "2025-12-04T09:30:00+00:00",
        "details": {"attachments": [{"attachment_type": "external", "filename": None,
                                     "title": "Estimates of station usage", "url": portal}]}})
    fake_http.add(portal, content=b'<script>(function (w,d,s,l,i) {})</script>'
                                  b'<a href="/media/1909/table-1410.csv">Table 1410 (CSV)</a>')
    fake_http.add("https://dataportal.orr.gov.uk/media/1909/table-1410.csv", content=CSV)
    out = call("preview_data", {"source": "govuk", "dataset_id": GOVUK_PAGE,
                                "resource_id": "estimates-of-station-usage"}).data
    assert [f["id"] for f in out["files"]] == ["estimates-of-station-usage/table-1410.csv"]
    rows = call("preview_data", {"source": "govuk", "dataset_id": GOVUK_PAGE,
                                 "resource_id": "estimates-of-station-usage/table-1410.csv"}).data["rows"]
    assert [r["name"] for r in rows] == ["A", "B", "C"]


def test_preview_can_group_and_sum(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    out = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1",
                                "group_by": ["year"], "sum": "value"}).data
    assert out["rows"] == [{"year": "2024", "value": 4, "row_count": 2}, {"year": "2025", "value": 2, "row_count": 1}]


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def dated_source(monkeypatch, fake_http):
    resources = [{"id": "r1", "name": "data", "format": "CSV", "url": "https://data.london.gov.uk/f.csv",
                  "updated": days_ago(400)},
                 {"id": "r2", "name": "undated", "format": "CSV", "url": "https://data.london.gov.uk/g.csv",
                  "updated": ""}]
    d = {**dataset("london", "x1", resources), "updated": days_ago(30)}
    hits = [{"source": "london", "id": "x1", "title": "Dated", "updated": days_ago(30)},
            {"source": "london", "id": "x2", "title": "Undated", "updated": ""}]
    monkeypatch.setattr(server, "SOURCES", {"london": fake_source("london", hits=hits, datasets={"x1": d})})
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    return fake_http


def test_search_hits_carry_their_age_in_days(dated_source):
    out = call("search_datasets", {"query": "x"}).data
    assert [h["age_days"] for h in out["results"]] == [30, None]


def test_describe_gives_the_age_of_the_dataset_and_each_file(dated_source):
    out = call("describe_dataset", {"source": "london", "dataset_id": "x1"}).data
    assert out["age_days"] == 30
    assert [r["age_days"] for r in out["resources"]] == [400, None]


def test_preview_gives_the_age_of_the_file_it_read(dated_source):
    out = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"}).data
    assert out["resource"]["age_days"] == 400


def test_files_found_on_a_webpage_carry_the_page_age(webpage_dataset):
    out = call("preview_data", {"source": "datagovuk", "dataset_id": "hh", "resource_id": "r1"}).data
    assert [f["age_days"] for f in out["files"]] == [None, None]  # the CKAN entry here has no dates


def test_preview_passes_sheets_through(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    result = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1", "sheets": ["a"]})
    assert result.is_error and "A CSV file has no sheets" in result.content[0].text


def test_search_policy_adds_age_days(monkeypatch):
    monkeypatch.setattr(server.govuk, "search_policy", lambda query, limit=10: [
        {"source": "govuk", "id": "/a", "type": "oral_statement", "updated": date.today().isoformat()}][:limit])
    out = call("search_policy", {"query": "x"}).data
    assert out["results"] == [{"source": "govuk", "id": "/a", "type": "oral_statement",
                               "updated": date.today().isoformat(), "age_days": 0}]


def test_read_page_errors_become_tool_errors(monkeypatch):
    def read_page(path, offset=0, attachment=None, find=None):
        raise LookupError(f"No GOV.UK page {path!r}") if offset == 0 else ValueError("offset 9 is past the end")
    monkeypatch.setattr(server.govuk, "read_page", read_page)
    missing = call("read_page", {"path": "/x"})
    assert missing.is_error and "No GOV.UK page '/x'" in missing.content[0].text
    past = call("read_page", {"path": "/x", "offset": 9})
    assert past.is_error and "past the end" in past.content[0].text


def test_read_page_passes_the_attachment_through(monkeypatch):
    monkeypatch.setattr(server.govuk, "read_page",
                        lambda path, offset=0, attachment=None, find=None: {"id": path, "offset": offset, "attachment": attachment})
    out = call("read_page", {"path": "/x", "offset": 5, "attachment": "r.pdf"}).data
    assert out == {"id": "/x", "offset": 5, "attachment": "r.pdf"}


def test_read_page_passes_find_through(monkeypatch):
    monkeypatch.setattr(server.govuk, "read_page",
                        lambda path, offset=0, attachment=None, find=None: {"id": path, "find": find})
    out = call("read_page", {"path": "/x", "find": "start date"}).data
    assert out == {"id": "/x", "find": "start date"}


def test_describe_returns_the_100_newest_files_and_preview_still_reads_an_older_one(monkeypatch, fake_http):
    start = date(2020, 1, 1)
    files = [{"id": f"f{i}.csv", "name": "", "format": "CSV", "url": f"https://data.london.gov.uk/f{i}.csv",
              "updated": (start + timedelta(days=i)).isoformat()} for i in range(150)]
    monkeypatch.setattr(server, "SOURCES", {"london": fake_source("london", datasets={"big": dataset("london", "big", files)})})
    out = call("describe_dataset", {"source": "london", "dataset_id": "big"}).data
    assert out["resources_total"] == 150
    assert [r["id"] for r in out["resources"]] == [f"f{i}.csv" for i in range(149, 49, -1)]
    fake_http.add("https://data.london.gov.uk/f0.csv", content=CSV)
    assert call("preview_data", {"source": "london", "dataset_id": "big", "resource_id": "f0.csv"}).data["total_rows"] == 3


def test_describe_of_a_small_dataset_has_no_total(sources):
    assert "resources_total" not in call("describe_dataset", {"source": "london", "dataset_id": "x1"}).data


def test_tfl_files_preview_end_to_end(fake_http):
    from tests.conftest import fixture_bytes
    for bucket in ("crowding", "cycling"):
        fake_http.add(f"https://s3.eu-west-1.amazonaws.com/{bucket}.data.tfl.gov.uk/", params={"list-type": "2"},
                      content=fixture_bytes(f"tfl/{bucket}_list.xml"))
    fake_http.add("https://crowding.data.tfl.gov.uk/Network%20Demand/StationFootfall_2025_2026%20.csv",
                  content=b"TravelDate,DayOfWeek,Station,EntryTapCount,ExitTapCount\n"
                          b"20250101,Wednesday,Abbey Road DLR,369,342\n20250101,Wednesday,Abbey Wood,5862,5603\n")
    hits = call("search_datasets", {"query": "footfall", "sources": ["tfl"]}).data["results"]
    assert hits[0]["id"] == "crowding/Network Demand"
    out = call("preview_data", {"source": "tfl", "dataset_id": "crowding/Network Demand",
                                "resource_id": "StationFootfall_2025_2026 .csv", "sort_by": "EntryTapCount",
                                "descending": True}).data
    assert out["rows"][0]["Station"] == "Abbey Wood"


@pytest.fixture
def pdf_source(monkeypatch, fake_http):
    from tests.test_source_govuk import make_pdf
    files = [{"id": "guide.pdf", "name": "User guide", "format": "PDF", "url": "https://crowding.data.tfl.gov.uk/BUSTO/guide.pdf"},
             {"id": "t.csv", "name": "Table", "format": "CSV", "url": "https://crowding.data.tfl.gov.uk/BUSTO/t.csv"},
             {"id": "evil.pdf", "name": "Off list", "format": "PDF", "url": "https://evil.example.com/x.pdf"}]
    monkeypatch.setattr(server, "SOURCES", {"tfl": fake_source("tfl", datasets={"crowding/BUSTO": dataset("tfl", "crowding/BUSTO", files)})})
    fake_http.add(files[0]["url"], content=make_pdf(["V/C is volume over capacity.", "TIMEBAND 1 is early morning."]))
    return fake_http


def test_read_page_reads_a_pdf_file_from_any_source(pdf_source):
    out = call("read_page", {"source": "tfl", "path": "crowding/BUSTO", "attachment": "guide.pdf"}).data
    assert out["text"] == "[page 1]\nV/C is volume over capacity.\n[page 2]\nTIMEBAND 1 is early morning."
    assert out["source"] == "tfl" and out["id"] == "crowding/BUSTO"
    assert out["attachment"] == {"id": "guide.pdf", "title": "User guide", "format": "PDF",
                                 "url": "https://crowding.data.tfl.gov.uk/BUSTO/guide.pdf"}
    assert out["next_offset"] is None and out["total_chars"] == len(out["text"])


def test_read_page_find_and_offset_work_on_another_sources_pdf(pdf_source, monkeypatch):
    found = call("read_page", {"source": "tfl", "path": "crowding/BUSTO", "attachment": "guide.pdf", "find": "timeband"}).data
    assert found["match_count"] == 1 and found["matches"][0]["page"] == 2
    monkeypatch.setattr(server.govuk, "MAX_CHARS", 10)
    first = call("read_page", {"source": "tfl", "path": "crowding/BUSTO", "attachment": "guide.pdf"}).data
    second = call("read_page", {"source": "tfl", "path": "crowding/BUSTO", "attachment": "guide.pdf",
                                "offset": first["next_offset"]}).data
    assert first["text"] + second["text"] == "[page 1]\nV/C is volu"


@pytest.mark.parametrize("args, message", [
    ({"attachment": "t.csv"}, "use preview_data"),
    ({"attachment": "nope.pdf"}, "guide.pdf"),
    ({}, "pass the dataset id as path and a PDF file id as attachment"),
    ({"attachment": "evil.pdf"}, "allowlist"),
])
def test_read_page_on_another_source_explains_what_went_wrong(pdf_source, args, message):
    out = call("read_page", {"source": "tfl", "path": "crowding/BUSTO", **args})
    assert out.is_error and message in out.content[0].text


def test_preview_passes_where_range_through(sources, fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    out = call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1",
                                "where_range": {"year": ["2025", ""]}}).data
    assert out["rows"] and all(r["year"] == "2025" for r in out["rows"])


GAP_RULE = "If no dataset answers the question, tell the user that ukdatapy found nothing and name what you searched"


def test_agents_are_told_to_try_ukdatapy_first_and_report_a_gap():
    async def run():
        async with Client(server.mcp) as client:
            tools = {t.name: t.description for t in await client.list_tools()}
            return client.instructions, tools["search_datasets"]
    instructions, description = asyncio.run(run())
    for text in (instructions, description):
        assert "before using any other source" in text
        assert GAP_RULE in text
