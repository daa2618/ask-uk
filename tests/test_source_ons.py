"""ONS adapter against fixtures recorded from ons.gov.uk on 2026-11-10, trimmed to the parts the adapter reads."""
import pytest
from conftest import fixture_bytes, fixture_json

from ukdatapy.core import http
from ukdatapy.sources import ons

SEARCH = "https://api.beta.ons.gov.uk/v1/search"
D7G7 = "/economy/inflationandpriceindices/timeseries/d7g7/mm23"
D7G7_CSV = f"https://www.ons.gov.uk/generator?format=csv&uri={D7G7}"
CPI_TABLES = "/economy/inflationandpriceindices/datasets/consumerpriceinflation"
TOPIC = "/economy/inflationandpriceindices"


def site(path):
    return "https://www.ons.gov.uk" + path


# search

def test_search_rows(fake_http):
    fake_http.add(SEARCH, params={"q": "cpi annual rate", "limit": "3"},
                  json=fixture_json("ons/search_cpi_annual_rate.json"))
    hits = ons.search("cpi annual rate", limit=3)
    assert [h["id"] for h in hits] == [
        "/economy/inflationandpriceindices/adhocs/2085contributionsofhousingcomponentstotheannualcpiinflationratejanuary2015toapril2024percentagepoints",
        "/economy/inflationandpriceindices/timeseries/d7nm/mm23",
        "/economy/inflationandpriceindices/timeseries/d7nn/mm23",
    ]
    goods = hits[1]
    assert goods == {
        "source": "ons",
        "id": "/economy/inflationandpriceindices/timeseries/d7nm/mm23",
        "title": "CPI ANNUAL RATE: Goods 2015=100",
        "summary": "timeseries | CDID D7NM | dataset MM23",
        "url": "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7nm/mm23",
        # The API sends 2026-10-20T23:00:00.000Z: midnight on 21 October in London (BST).
        "updated": "2026-10-21",
    }
    assert hits[0]["summary"] == "static_adhoc"


def test_search_sends_ascii_only(fake_http):
    # The API answers 400 "invalid characters in query" for £, € and é.
    fake_http.add(SEARCH, params={"q": "house prices", "limit": "10"}, json={"count": 0, "items": []})
    assert ons.search("house £ prices é") == []


def test_search_summary_is_capped_at_300(fake_http):
    item = {"type": "bulletin", "title": "T", "uri": "/x/bulletins/y", "summary": "word " * 200,
            "release_date": ""}
    fake_http.add(SEARCH, params={"q": "y", "limit": "10"}, json={"count": 1, "items": [item]})
    hit = ons.search("y")[0]
    assert hit["summary"].startswith("bulletin | word word")
    assert len(hit["summary"]) <= 300
    assert hit["updated"] == ""


def test_search_falls_back_to_meta_description(fake_http):
    item = {"type": "dataset_landing_page", "title": "T", "uri": "/x/datasets/y", "summary": "",
            "meta_description": "Monthly tables.", "release_date": "2026-01-14T07:00:00.000Z"}
    fake_http.add(SEARCH, params={"q": "y", "limit": "10"}, json={"count": 1, "items": [item]})
    hit = ons.search("y")[0]
    assert hit["summary"] == "dataset_landing_page | Monthly tables."
    assert hit["updated"] == "2026-01-14"


# describe

def test_describe_time_series_page(fake_http):
    fake_http.add(site(D7G7), content=fixture_bytes("ons/d7g7.html"))
    got = ons.describe(D7G7)
    assert got == {
        "source": "ons",
        "id": D7G7,
        "title": "CPI ANNUAL RATE 00: ALL ITEMS 2015=100",
        "description": "",
        "publisher": "Office for National Statistics",
        "updated": "2026-10-21",
        "url": site(D7G7),
        "resources": [{"id": "series_csv", "name": "CPI ANNUAL RATE 00: ALL ITEMS 2015=100 (CSV)", "format": "CSV",
                       "url": D7G7_CSV, "updated": "2026-10-21"}],
    }


def test_describe_accepts_a_full_url(fake_http):
    fake_http.add(site(D7G7), content=fixture_bytes("ons/d7g7.html"))
    assert ons.describe(site(D7G7) + "/")["id"] == D7G7


def test_describe_dataset_page_lists_files(fake_http):
    fake_http.add(site(CPI_TABLES), content=fixture_bytes("ons/consumerpriceinflation.html"))
    got = ons.describe(CPI_TABLES)
    assert got["title"] == "Consumer price inflation tables"
    assert got["description"].startswith("Measures of monthly UK inflation data including CPIH, CPI and RPI.")
    assert got["updated"] == "2026-10-21"
    base = site("/file?uri=" + CPI_TABLES + "/current/")
    # The page's own /current edition and the Welsh-language copy are not resources.
    assert got["resources"] == [
        {"id": "consumerpriceinflationdetailedreferencetables.xlsx",
         "name": "Download Consumer price inflation tables : Current in xlsx format", "format": "XLSX",
         "url": base + "consumerpriceinflationdetailedreferencetables.xlsx", "updated": "2026-10-21"},
        {"id": "cpirereferenceddata2015100tcm77432809.xls",
         "name": "Re-referencing of the CPI and CPIH indices to 2015=100", "format": "XLS",
         "url": base + "cpirereferenceddata2015100tcm77432809.xls", "updated": "2026-10-21"},
    ]


def test_describe_topic_page_lists_dataset_pages(fake_http):
    fake_http.add(site(TOPIC), content=fixture_bytes("ons/inflationandpriceindices.html"))
    got = ons.describe(TOPIC)
    assert got["title"] == "Inflation and price indices"
    assert got["updated"] == ""
    assert len(got["resources"]) == 6
    assert got["resources"][1] == {
        "id": "consumerpriceindices", "name": "Consumer price inflation time series", "format": "PAGE",
        "url": site("/economy/inflationandpriceindices/datasets/consumerpriceindices"), "updated": ""}
    assert all(r["format"] == "PAGE" for r in got["resources"])


def test_describe_deduplicates_ids_and_urls(fake_http):
    html = """<html><body><h1>Page</h1>
    <a href="/file?uri=/a/datasets/x/2025/table.xlsx">2025</a>
    <a href="/file?uri=/a/datasets/x/2025/table.xlsx">2025 again</a>
    <a href="/file?uri=/a/datasets/x/2024/table.xlsx">2024</a>
    <a href="https://www.ons.gov.uk/a/datasets/x/data.csv">Direct CSV</a>
    <a href="/a/datasets/xother">Sibling dataset whose slug extends this page's</a>
    </body></html>"""
    fake_http.add(site("/a/datasets/x"), text=html)
    resources = ons.describe("/a/datasets/x")["resources"]
    assert [(r["id"], r["format"]) for r in resources] == [
        ("table.xlsx", "XLSX"), ("table.xlsx-2", "XLSX"), ("data.csv", "CSV"), ("xother", "PAGE")]


def test_describe_keeps_a_direct_ods_link(fake_http):
    html = '<html><body><h1>Page</h1><a href="https://www.ons.gov.uk/a/datasets/x/data.ods">ODS</a></body></html>'
    fake_http.add(site("/a/datasets/x"), text=html)
    resources = ons.describe("/a/datasets/x")["resources"]
    assert [(r["id"], r["format"]) for r in resources] == [("data.ods", "ODS")]


def test_describe_unknown_page_raises_lookup_error(fake_http):
    fake_http.add(site("/economy/datasets/nosuch"), status=404)
    with pytest.raises(LookupError):
        ons.describe("/economy/datasets/nosuch")


def test_describe_server_error_is_not_a_lookup_error(fake_http):
    fake_http.add(site("/economy/datasets/busy"), status=500)
    with pytest.raises(http.HttpError):
        ons.describe("/economy/datasets/busy")


@pytest.mark.parametrize("bad", ["https://data.gov.uk/dataset/x", "economy/no-leading-slash", ""])
def test_describe_refuses_other_hosts_without_a_request(fake_http, bad):
    with pytest.raises(LookupError):
        ons.describe(bad)
    assert fake_http.calls == []


# get_series

def test_get_series(fake_http):
    fake_http.add(D7G7_CSV, content=fixture_bytes("ons/d7g7.csv"))
    got = ons.get_series(D7G7)
    obs = got.pop("observations")
    assert got == {
        "title": "CPI ANNUAL RATE 00: ALL ITEMS 2015=100",
        "cdid": "D7G7",
        "unit": "%",
        "release_date": "2026-10-21",
        "next_release": "2026-11-18",
        "dataset": "MM23",
        "url": site(D7G7),
    }
    assert obs[0] == {"period": "1989", "value": 5.2, "frequency": "year"}
    assert obs[3] == {"period": "1989 Q1", "value": 5.0, "frequency": "quarter"}
    assert obs[-1] == {"period": "2026 SEP", "value": 3.5, "frequency": "month"}
    assert [o["frequency"] for o in obs] == ["year"] * 3 + ["quarter"] * 3 + ["month"] * 3
    assert fake_http.calls[0]["url"] == D7G7_CSV


def test_get_series_accepts_a_full_url(fake_http):
    fake_http.add(D7G7_CSV, content=fixture_bytes("ons/d7g7.csv"))
    assert ons.get_series(site(D7G7))["cdid"] == "D7G7"


def test_get_series_reads_a_long_notes_field(fake_http):
    # The csv module refuses fields over 131072 characters unless the limit is raised.
    notes = "n" * 200_000
    text = f'"Title","T"\n"CDID","ABCD"\n"Important notes","{notes}"\n"2020","1.5"\n'
    fake_http.add("https://www.ons.gov.uk/generator?format=csv&uri=/a/timeseries/abcd/xx", text=text)
    got = ons.get_series("/a/timeseries/abcd/xx")
    assert got["observations"] == [{"period": "2020", "value": 1.5, "frequency": "year"}]


def test_get_series_refuses_a_page_that_is_not_a_time_series(fake_http):
    with pytest.raises(ValueError):
        ons.get_series(CPI_TABLES)
    assert fake_http.calls == []


def test_series_path_cannot_add_query_parameters(fake_http):
    with pytest.raises(AssertionError, match="No fake route"):
        ons.get_series("/a/timeseries/abcd/xx&format=xlsx")
    assert "&format=xlsx" not in fake_http.calls[0]["url"]
    assert "%26format%3Dxlsx" in fake_http.calls[0]["url"]
