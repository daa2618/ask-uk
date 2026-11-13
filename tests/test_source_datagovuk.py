"""data.gov.uk adapter, against CKAN responses recorded from ckan.publishing.service.gov.uk."""
import pytest

from tests.conftest import fixture_json
from ukdatapy.core import http
from ukdatapy.sources import datagovuk

API = "https://ckan.publishing.service.gov.uk/api/3/action"


def add_show(fake_http, dataset_id, fixture=None, status=200):
    fake_http.add(f"{API}/package_show", params={"id": dataset_id}, status=status,
                  json=fixture_json(fixture or f"datagovuk/package_show_{dataset_id}.json"))


def test_search_maps_ckan_packages_to_hits(fake_http):
    fake_http.add(f"{API}/package_search", params={"q": "road traffic counts", "rows": 3},
                  json=fixture_json("datagovuk/package_search_road-traffic-counts.json"))
    hits = datagovuk.search("road traffic counts", limit=3)
    assert [h["id"] for h in hits] == ["road-traffic-counts", "gb-road-traffic-counts", "traffic-count1"]
    hit = hits[1]
    assert hit["source"] == "datagovuk"
    assert hit["title"] == "GB Road Traffic Counts"
    assert hit["url"] == "https://www.data.gov.uk/dataset/208c0e7b-353f-4e2d-8b7a-1a7118467acc/gb-road-traffic-counts"
    assert hit["updated"] == "2025-06-15"
    assert hit["summary"].startswith("Department for Transport: The Department for Transport collects traffic data")
    assert "<p>" not in hit["summary"]
    assert all(len(h["summary"]) <= 300 for h in hits)
    assert set(hit) == {"source", "id", "title", "summary", "url", "updated"}


def test_search_sends_limit_as_rows(fake_http):
    fake_http.add(f"{API}/package_search", params={"q": "x", "rows": 10}, json={"success": True, "result": {"results": []}})
    assert datagovuk.search("x") == []


def test_describe_road_traffic(fake_http):
    add_show(fake_http, "gb-road-traffic-counts")
    d = datagovuk.describe("gb-road-traffic-counts")
    assert d["source"] == "datagovuk"
    assert d["id"] == "gb-road-traffic-counts"
    assert d["title"] == "GB Road Traffic Counts"
    assert d["publisher"] == "Department for Transport"
    assert d["updated"] == "2025-06-15"
    assert d["url"] == "https://www.data.gov.uk/dataset/208c0e7b-353f-4e2d-8b7a-1a7118467acc/gb-road-traffic-counts"
    assert len(d["description"]) <= 1000 and "<li>" not in d["description"]
    assert len(d["resources"]) == 11
    first = d["resources"][0]
    assert first == {
        "id": "211d7d1b-6573-4de2-9dde-3d84ceeb2f61",
        "name": "Road traffic data metadata",
        "format": "PDF",
        "url": "https://storage.googleapis.com/dft-statistics/road-traffic/all-traffic-data-metadata.pdf",
        "updated": "2014-08-14",
    }
    formats = [r["format"] for r in d["resources"]]
    assert formats[8] == "CSV"  # CKAN says ".csv"
    assert formats[10] == "CSV/ZIP"  # CKAN says "csv/zip"; kept, only upper-cased
    assert len({r["id"] for r in d["resources"]}) == 11


def test_describe_fly_tipping_null_names(fake_http):
    add_show(fake_http, "fly-tipping_in_england_")
    d = datagovuk.describe("fly-tipping_in_england_")
    assert d["publisher"] == "Department for Environment, Food and Rural Affairs"
    assert d["updated"] == "2026-03-02"
    assert d["description"].startswith("A summary of fly-tipping incidents in England")
    assert d["resources"][0]["name"] == ""  # CKAN name is null
    assert d["resources"][0]["format"] == "HTML"
    assert d["resources"][2]["url"].startswith("http://data.defra.gov.uk/")  # passed through as published


def test_resource_updated_prefers_last_modified(fake_http):
    pkg = fixture_json("datagovuk/package_show_fly-tipping_in_england_.json")
    pkg["result"]["resources"][0]["last_modified"] = "2024-02-03T10:00:00"
    fake_http.add(f"{API}/package_show", params={"id": "fly-tipping_in_england_"}, json=pkg)
    assert datagovuk.describe("fly-tipping_in_england_")["resources"][0]["updated"] == "2024-02-03"


def test_describe_without_organization(fake_http):
    pkg = fixture_json("datagovuk/package_show_fly-tipping_in_england_.json")
    pkg["result"]["organization"] = None
    fake_http.add(f"{API}/package_show", params={"id": "fly-tipping_in_england_"}, json=pkg)
    assert datagovuk.describe("fly-tipping_in_england_")["publisher"] == ""


def test_describe_unknown_id_404_raises_lookuperror(fake_http):
    add_show(fake_http, "no-such-dataset-xyz", fixture="datagovuk/package_show_not-found.json", status=404)
    with pytest.raises(LookupError):
        datagovuk.describe("no-such-dataset-xyz")


def test_describe_success_false_raises_lookuperror(fake_http):
    add_show(fake_http, "no-such-dataset-xyz", fixture="datagovuk/package_show_not-found.json", status=200)
    with pytest.raises(LookupError):
        datagovuk.describe("no-such-dataset-xyz")


def test_describe_server_error_is_not_lookuperror(fake_http):
    fake_http.add(f"{API}/package_show", params={"id": "x"}, status=500, text="boom")
    with pytest.raises(http.HttpError):
        datagovuk.describe("x")
