"""London Datastore adapter: search and describe over the daily-cached catalogue export, offline."""
import json

import pytest
from conftest import fixture_json

from ukdatapy.core.paths import cache_dir
from ukdatapy.sources import london

EXPORT = "https://data.london.gov.uk/api/v2/datasets/export.json"


@pytest.fixture
def export(fake_http):
    fake_http.add(EXPORT, json=fixture_json("london/export.json"))
    return fake_http


def _dataset(id_, title="", tags=(), description="", updated="2020-01-01T00:00:00.000Z"):
    return {"id": id_, "slug": id_, "title": title, "tags": list(tags), "description": description,
            "updatedAt": updated, "publisher": "P", "webpage": f"https://data.london.gov.uk/dataset/{id_}",
            "resources": {}}


def test_search_hit_shape(export):
    hits = london.search("house prices")
    assert hits[0] == {
        "source": "london",
        "id": "2jkxd",
        "title": "Average House Prices by Borough, Ward, MSOA & LSOA",
        "summary": hits[0]["summary"],
        "url": "https://data.london.gov.uk/dataset/2jkxd",
        "updated": "2018-11-05",
    }
    assert hits[0]["summary"].startswith("Annual mean and median property prices")


def test_summary_is_plain_text_at_most_300_characters(export):
    for hit in london.search("borough"):
        assert len(hit["summary"]) <= 300
        assert "<" not in hit["summary"]


def test_ties_go_to_the_most_recently_updated(export):
    # All three match "density" in title, tags and description; the fixture lists them oldest first.
    assert [h["id"] for h in london.search("density")] == ["2jk0d", "2lzj5", "e1zp8"]


def test_query_word_matches_inside_a_hyphenated_tag_and_ignores_case(export):
    # "economic" appears only in the 2jk0d tag "borough-economic".
    assert [h["id"] for h in london.search("ECONOMIC")] == ["2jk0d"]


def test_title_outweighs_tags_which_outweigh_description(fake_http):
    same_day = "2020-01-01T00:00:00.000Z"
    fake_http.add(EXPORT, json=[
        _dataset("desc", description="<p>Crime counts</p>", updated=same_day),
        _dataset("tags", tags=["crime"], updated=same_day),
        _dataset("title", title="Crime", updated=same_day),
        _dataset("none", title="Weather", updated=same_day),
    ])
    assert [h["id"] for h in london.search("crime")] == ["title", "tags", "desc"]


def test_no_match_and_limit(export):
    assert london.search("zzzzqqq") == []
    assert len(london.search("density", limit=2)) == 2


def test_describe(export):
    d = london.describe("e1zp8")
    assert {k: d[k] for k in ("source", "id", "title", "publisher", "updated", "url")} == {
        "source": "london",
        "id": "e1zp8",
        "title": "Land Area and Population Density, Ward and Borough",
        "publisher": "Greater London Authority",
        "updated": "2018-03-05",
        "url": "https://data.london.gov.uk/dataset/e1zp8",
    }
    assert d["description"].startswith("Land area and population density figures for 2001 to 2050")
    assert d["resources"][0] == {
        "id": "77e9257d-ad9d-47aa-aeed-59a00741f301",
        "name": "Land Area & Population - Borough (CSV Version)",
        "format": "CSV",
        "url": "https://data.london.gov.uk/download/e1zp8/77e9257d-ad9d-47aa-aeed-59a00741f301/housing-density-borough.csv",
        "updated": "2018-03-05",
    }
    # The export says "spreadsheet"; the file extension gives the contract's format.
    assert d["resources"][1]["format"] == "XLSX"
    assert len({r["id"] for r in d["resources"]}) == len(d["resources"]) == 3


def test_describe_trims_description_and_handles_missing_publisher(export):
    assert len(london.describe("2jk0d")["description"]) <= 1000
    d = london.describe("e70wm")
    assert d["publisher"] == ""
    assert d["resources"][0]["format"] == "PNG"  # export format "image"


def test_unknown_id_raises_lookup_error(export):
    with pytest.raises(LookupError):
        london.describe("nope0")


def test_second_call_on_the_same_day_does_not_refetch(export):
    london.search("density")
    london.describe("e1zp8")
    london.search("prices")
    assert len(export.calls) == 1


def test_cache_keeps_only_the_needed_fields(export):
    london.search("density")
    (cached,) = cache_dir().glob("london*.json")
    text = cached.read_text()
    for dropped in ("check_hash", "licence", "temporal_coverage_from", "dp-prose", "canonical"):
        assert dropped not in text
    assert len(text) < len(json.dumps(fixture_json("london/export.json")))
