"""TfL open data adapter, against S3 bucket listings recorded from crowding and cycling.data.tfl.gov.uk on 2026-11-12."""
import pytest
from conftest import fixture_bytes

from ukdatapy.sources import tfl

LIST = "https://s3.eu-west-1.amazonaws.com/{}/"


@pytest.fixture
def buckets(fake_http):
    fake_http.add(LIST.format("crowding.data.tfl.gov.uk"), params={"list-type": "2"},
                  content=fixture_bytes("tfl/crowding_list.xml"))
    fake_http.add(LIST.format("cycling.data.tfl.gov.uk"), params={"list-type": "2"},
                  content=fixture_bytes("tfl/cycling_list.xml"))
    return fake_http


def test_footfall_finds_the_network_demand_folder_first(buckets):
    hit = tfl.search("station footfall")[0]
    assert hit["source"] == "tfl"
    assert hit["id"] == "crowding/Network Demand"
    assert hit["title"] == "TfL Network Demand"
    assert hit["updated"] == "2026-11-11"
    assert "StationFootfall_2025_2026 .csv" in hit["summary"] and len(hit["summary"]) <= 300


def test_words_inside_camel_case_file_names_match(buckets):
    # In the Annual Station Counts folders, "entry" appears only inside AC20xx_AnnualisedEntryExit*.xlsx.
    ids = {h["id"] for h in tfl.search("entry")}
    assert {"crowding/Annual Station Counts/2025", "crowding/Annual Station Counts/2024"} <= ids


def test_the_folder_description_is_searched(buckets):
    # BUSTO file names never say "bus"; the hand-written description does.
    assert {h["id"] for h in tfl.search("bus demand")} >= {"crowding/BUSTO", "crowding/BUSTO/2025-2026"}
    assert tfl.search("cycle hire journeys")[0]["id"] == "cycling/usage-stats"


def test_files_at_the_bucket_root_and_folder_markers_are_not_datasets(buckets):
    ids = {h["id"] for h in tfl.search("index html annual station counts", limit=20)}
    assert "crowding/" not in ids and "crowding" not in ids
    assert "crowding/Annual Station Counts" not in ids  # holds only the folder marker key


def test_describe_lists_a_folders_files_newest_first_with_encoded_urls(buckets):
    d = tfl.describe("crowding/Network Demand")
    assert d["publisher"] == "Transport for London"
    assert [r["id"] for r in d["resources"]] == ["StationFootfall_2025_2026 .csv", "Journeys_2019.csv"]
    footfall = d["resources"][0]
    assert footfall["url"] == "https://crowding.data.tfl.gov.uk/Network%20Demand/StationFootfall_2025_2026%20.csv"
    assert footfall["format"] == "CSV"
    assert footfall["bytes"] > 10_000_000
    assert footfall["updated"] == "2026-11-11"


def test_unreadable_formats_are_listed(buckets):
    assert [r["format"] for r in tfl.describe("crowding/BUSTO")["resources"]] == ["PDF"]


def test_describe_an_unknown_folder_is_a_lookup_error(buckets):
    with pytest.raises(LookupError):
        tfl.describe("crowding/Nope")


def test_listing_follows_continuation_tokens(fake_http):
    ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
    page = ('<ListBucketResult {ns}><IsTruncated>{t}</IsTruncated>{tok}<Contents><Key>{k}</Key>'
            '<LastModified>2026-01-01T00:00:00.000Z</LastModified><Size>1</Size></Contents></ListBucketResult>')
    crowd = LIST.format("crowding.data.tfl.gov.uk")
    fake_http.add(crowd, params={"list-type": "2"},
                  text=page.format(ns=ns, t="true", tok="<NextContinuationToken>abc</NextContinuationToken>", k="RODS/a.csv"))
    fake_http.add(crowd, params={"list-type": "2", "continuation-token": "abc"},
                  text=page.format(ns=ns, t="false", tok="", k="RODS/b.csv"))
    fake_http.add(LIST.format("cycling.data.tfl.gov.uk"), params={"list-type": "2"},
                  text=f'<ListBucketResult {ns}><IsTruncated>false</IsTruncated></ListBucketResult>')
    assert [r["id"] for r in tfl.describe("crowding/RODS")["resources"]] == ["a.csv", "b.csv"]


def test_the_listing_is_fetched_once_a_day(buckets):
    tfl.search("footfall")
    tfl.describe("crowding/Network Demand")
    assert len(buckets.calls) == 2  # one listing per bucket


@pytest.mark.parametrize("query", ["average weekly earnings by year", "crime rates by borough",
                                   "population of london boroughs", "house prices in london data"])
def test_questions_about_other_topics_find_nothing(buckets, query):
    # Every search_datasets call fans out to tfl, so a shared word such as "by", "of" or "london" must not match.
    assert tfl.search(query) == []


def test_half_the_meaningful_words_are_enough(buckets):
    # An agent's query mixes a word TfL uses ("footfall") with one it does not ("pedestrian").
    assert tfl.search("pedestrian footfall in london")[0]["id"] == "crowding/Network Demand"
