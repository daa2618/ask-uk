"""GOV.UK adapter, against Search API and Content API responses recorded from www.gov.uk on 2026-11-11."""
from datetime import date, timedelta

import pytest

from tests.conftest import fixture_json
from ukdatapy.sources import govuk

SEARCH = ("https://www.gov.uk/api/search.json?q={q}&count={n}&fields=title&fields=link&fields=description"
          "&fields=public_timestamp&fields=organisations&fields=content_store_document_type"
          "&filter_content_store_document_type=statistical_data_set"
          "&filter_content_store_document_type=official_statistics"
          "&filter_content_store_document_type=national_statistics")
CONTENT = "https://www.gov.uk/api/content"
TABLES = "/government/statistical-data-sets/immigration-system-statistics-data-tables"
RELEASE = "/government/statistics/immigration-system-statistics-year-ending-june-2026"


def add_search(fake_http, q, n, all_time, past_year):
    """Both searches govuk.search sends: best matches of any date, and best matches from the past year."""
    fake_http.add(SEARCH.format(q=q, n=n), json=all_time)
    since = (date.today() - timedelta(days=365)).isoformat()
    fake_http.add(SEARCH.format(q=q, n=n) + f"&filter_public_timestamp=from%3A{since}", json=past_year)


def result(link, when, score):
    return {"link": link, "title": link, "public_timestamp": when + "T09:30:00+01:00", "es_score": score}


def add_content(fake_http, path, fixture):
    fake_http.add(CONTENT + path, json=fixture_json(f"govuk/{fixture}"))


def test_search_maps_results_to_hits(fake_http):
    add_search(fake_http, "immigration+system+statistics+data+tables", 3,
               fixture_json("govuk/search_immigration-system-statistics-data-tables.json"), {"results": []})
    hits = govuk.search("immigration system statistics data tables", limit=3)
    assert [h["id"] for h in hits] == [
        TABLES,
        TABLES + "-year-ending-march-2023",
        TABLES + "-year-ending-december-2022",
    ]
    hit = hits[0]
    assert hit["source"] == "govuk"
    assert hit["title"] == "Immigration system statistics data tables"
    assert hit["url"] == "https://www.gov.uk" + TABLES
    assert hit["updated"] == "2026-08-27"
    assert hit["summary"].startswith("Home Office: ")
    assert hit["score"] == 0.26358518 and hit["relevance_rank"] == 1
    assert set(hit) == {"source", "id", "title", "summary", "url", "updated", "score", "relevance_rank"}
    assert all(len(h["summary"]) <= 300 for h in hits)


def test_search_sends_limit_as_count(fake_http):
    add_search(fake_http, "x", 10, {"results": [], "total": 0}, {"results": [], "total": 0})
    assert govuk.search("x") == []
    assert len(fake_http.calls) == 2


def test_search_merges_both_searches_newest_first_and_ranks_by_score(fake_http):
    # The best match is an old edition; the past-year search finds two newer pages that match less well.
    add_search(fake_http, "house+building", 2,
               {"results": [result("/old-2018", "2018-06-28", 0.08), result("/old-2011", "2011-08-18", 0.07)]},
               {"results": [result("/new-a", "2026-04-22", 0.03), result("/new-b", "2026-10-24", 0.02)]})
    hits = govuk.search("house building", limit=2)
    assert [(h["id"], h["relevance_rank"]) for h in hits] == [
        ("/new-b", 4), ("/new-a", 3), ("/old-2018", 1), ("/old-2011", 2)]
    assert [h["score"] for h in hits] == [0.02, 0.03, 0.08, 0.07]


def test_a_page_found_by_both_searches_is_listed_once(fake_http):
    both = result("/tables", "2026-08-27", 0.26)
    add_search(fake_http, "x", 2, {"results": [both, result("/old", "2023-06-20", 0.17)]}, {"results": [both]})
    assert [h["id"] for h in govuk.search("x", limit=2)] == ["/tables", "/old"]


def test_describe_lists_each_attachment_as_a_resource(fake_http):
    add_content(fake_http, TABLES, "content_immigration-system-statistics-data-tables.json")
    d = govuk.describe(TABLES)
    assert d["source"] == "govuk" and d["id"] == TABLES
    assert d["title"] == "Immigration system statistics data tables"
    assert d["publisher"] == "Home Office"
    assert d["updated"] == "2026-08-27"
    assert d["url"] == "https://www.gov.uk" + TABLES
    assert d["description"] == "Listing of the data tables included in 'Immigration system statistics'."
    assert len(d["resources"]) == 48
    assert len({r["id"] for r in d["resources"]}) == 48
    visas = next(r for r in d["resources"] if r["id"] == "entry-clearance-visa-outcomes-datasets-jun-2026.xlsx")
    assert visas == {
        "id": "entry-clearance-visa-outcomes-datasets-jun-2026.xlsx",
        "name": "Entry clearance visa applications and outcomes detailed datasets, year ending June 2026",
        "format": "XLSX",
        "url": "https://assets.publishing.service.gov.uk/media/6a85c3741b45e4e555421eda/"
               "entry-clearance-visa-outcomes-datasets-jun-2026.xlsx",
        "updated": "2026-08-27",
    }
    assert {r["format"] for r in d["resources"]} == {"XLSX", "ODS"}


def test_html_attachments_get_an_absolute_url_and_html_format(fake_http):
    add_content(fake_http, RELEASE, "content_immigration-system-statistics-year-ending-june-2026.json")
    resources = govuk.describe(RELEASE)["resources"]
    html = [r for r in resources if r["format"] == "HTML"]
    assert html and all(r["url"].startswith("https://www.gov.uk/government/") for r in html)
    assert len({r["id"] for r in resources}) == len(resources)


def test_attachments_that_share_a_file_name_get_numbered_ids(fake_http):
    fake_http.add(CONTENT + "/government/x", json={"base_path": "/government/x", "details": {"attachments": [
        {"attachment_type": "file", "filename": "t.csv", "url": "https://assets.publishing.service.gov.uk/1/t.csv"},
        {"attachment_type": "external", "url": "https://example.com/t.csv"}]}})
    assert [r["id"] for r in govuk.describe("/government/x")["resources"]] == ["t.csv", "t.csv-2"]


def test_an_external_link_without_a_file_name_is_a_webpage(fake_http):
    # Shape recorded from the ORR "Estimates of station usage: April 2024 to March 2025" page on 2026-11-12.
    fake_http.add(CONTENT + "/government/x", json={"base_path": "/government/x", "details": {"attachments": [
        {"attachment_type": "external", "filename": None, "title": "Estimates of station usage",
         "url": "https://dataportal.orr.gov.uk/statistics/usage/estimates-of-station-usage"},
        {"attachment_type": "external", "url": "https://example.com/t.csv"}]}})
    assert [r["format"] for r in govuk.describe("/government/x")["resources"]] == ["HTML", "CSV"]


def test_describe_accepts_a_path_without_its_leading_slash(fake_http):
    add_content(fake_http, TABLES, "content_immigration-system-statistics-data-tables.json")
    assert govuk.describe(TABLES.lstrip("/"))["id"] == TABLES


def test_describe_unknown_page_is_a_lookup_error(fake_http):
    fake_http.add(CONTENT + "/government/statistics/no-such-page", status=404)
    with pytest.raises(LookupError, match="No GOV.UK page '/government/statistics/no-such-page'"):
        govuk.describe("/government/statistics/no-such-page")


def test_describe_refuses_an_id_that_is_a_url_or_has_a_query(fake_http):
    for bad in ("https://evil.example.com/x", "/government/x?y=1", "/government/x#y", "//evil.example.com/x"):
        with pytest.raises(LookupError, match="GOV.UK page path"):
            govuk.describe(bad)
    assert fake_http.calls == []


# Policy pages (plan 005), against responses recorded from www.gov.uk on 2026-11-12.
POLICY_SEARCH = ("https://www.gov.uk/api/search.json?q={q}&count={n}&fields=title&fields=link&fields=description"
                 "&fields=public_timestamp&fields=organisations&fields=content_store_document_type"
                 + "".join(f"&filter_content_store_document_type={t}" for t in (
                     "oral_statement", "written_statement", "speech", "open_consultation", "closed_consultation",
                     "consultation_outcome", "policy_paper", "news_story", "press_release", "guidance",
                     "detailed_guide", "statutory_guidance", "impact_assessment", "government_response")))
STATEMENT = "/government/speeches/home-secretary-earned-settlement-statement"
CONSULTATION = "/government/consultations/earned-settlement"


def test_search_policy_sends_the_policy_types_and_returns_each_hit_type(fake_http):
    fake_http.add(POLICY_SEARCH.format(q="earned+settlement", n=3),
                  json=fixture_json("govuk/search_policy_earned-settlement.json"))
    since = (date.today() - timedelta(days=365)).isoformat()
    fake_http.add(POLICY_SEARCH.format(q="earned+settlement", n=3) + f"&filter_public_timestamp=from%3A{since}",
                  json={"results": []})
    hits = govuk.search_policy("earned settlement", limit=3)
    assert {h["id"]: h["type"] for h in hits} == {
        "/government/news/home-secretary-unveils-new-rules-for-migrants-settling-in-the-uk": "news_story",
        STATEMENT: "oral_statement",
        CONSULTATION: "consultation_outcome",
    }
    statement = next(h for h in hits if h["id"] == STATEMENT)
    assert statement["source"] == "govuk" and statement["updated"] == "2026-11-02"
    assert statement["summary"].startswith("Home Office: ")


def test_read_page_returns_a_statement_as_plain_text(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    page = govuk.read_page(STATEMENT)
    assert page["id"] == STATEMENT and page["type"] == "oral_statement"
    assert page["title"]
    assert page["publisher"] == "Home Office"
    assert page["published"] == "2026-11-02" and page["updated"] == "2026-11-02"
    assert page["url"] == "https://www.gov.uk" + STATEMENT
    assert page["text"].startswith("Mr Speaker, with permission, I will make a statement")
    assert "<" not in page["text"] and "\n\n" not in page["text"]
    assert page["offset"] == 0 and page["next_offset"] is None
    assert page["total_chars"] == len(page["text"])
    assert page["attachments"] == []


def test_read_page_adds_the_consultation_outcome_and_lists_attachments(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    page = govuk.read_page(CONSULTATION)
    assert "Settlement refers to a person’s right to live in the UK permanently" in page["text"]
    assert "The starting point for settlement will increase from 5 to 10 years." in page["text"]
    # A link inside a paragraph stays on that paragraph's line.
    assert ("The 2025 UK Immigration White Paper, Restoring Control over the Immigration System, introduced"
            in page["text"])
    html, command_paper, response = page["attachments"]
    assert html["format"] == "HTML" and html["id"] == (
        CONSULTATION + "/a-fairer-pathway-to-settlement-statement-and-accompanying-consultation-on-earned-settlement-accessible")
    assert response["format"] == "PDF"
    assert response["title"] == "A Fairer Pathway to Settlement: Government response to the consultation"
    assert response["url"].startswith("https://assets.publishing.service.gov.uk/media/")
    assert command_paper["format"] == "PDF"


def test_read_page_pages_long_text_with_next_offset(fake_http, monkeypatch):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    whole = govuk.read_page(STATEMENT)["text"]
    monkeypatch.setattr(govuk, "MAX_CHARS", 1000)
    first = govuk.read_page(STATEMENT)
    assert len(first["text"]) == 1000 and first["next_offset"] == 1000
    parts, page = [first["text"]], first
    while page["next_offset"] is not None:
        page = govuk.read_page(STATEMENT, offset=page["next_offset"])
        parts.append(page["text"])
    assert "".join(parts) == whole
    assert page["total_chars"] == len(whole)


def test_read_page_offset_past_the_end_is_an_error(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    with pytest.raises(ValueError, match="past the end of the page"):
        govuk.read_page(STATEMENT, offset=10**6)


def test_read_page_unknown_page_is_a_lookup_error(fake_http):
    fake_http.add(CONTENT + "/government/speeches/no-such-speech", status=410)
    with pytest.raises(LookupError, match="No GOV.UK page"):
        govuk.read_page("/government/speeches/no-such-speech")


def test_read_page_refuses_a_url(fake_http):
    with pytest.raises(LookupError, match="GOV.UK page path"):
        govuk.read_page("https://evil.example.com/x")
    assert fake_http.calls == []


# PDF attachments (plan 005 D5).
RESPONSE_PDF = "A_Fairer_Pathway_to_Settlement_-_Government_response_to_the_consultation.pdf"
RESPONSE_URL = ("https://assets.publishing.service.gov.uk/media/6ae90b0e5b14ba0182a272a4/" + RESPONSE_PDF)


def make_pdf(pages):
    """A minimal PDF with one line of Helvetica text per page (an empty string makes a page with no text)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_read_page_reads_a_pdf_attachment_with_page_markers(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    fake_http.add(RESPONSE_URL, content=make_pdf(["Before implementation, guidance will follow.", "Annex A"]))
    page = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF)
    assert page["text"] == "[page 1]\nBefore implementation, guidance will follow.\n[page 2]\nAnnex A"
    assert page["attachment"] == {
        "id": RESPONSE_PDF, "format": "PDF", "url": RESPONSE_URL,
        "title": "A Fairer Pathway to Settlement: Government response to the consultation"}
    assert page["id"] == CONSULTATION and page["type"] == "consultation_outcome"
    assert page["total_chars"] == len(page["text"]) and page["next_offset"] is None


def test_read_page_pages_a_pdf_and_caches_it_for_the_day(fake_http, monkeypatch):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    fake_http.add(RESPONSE_URL, content=make_pdf(["Before implementation, guidance will follow.", "Annex A"]))
    monkeypatch.setattr(govuk, "MAX_CHARS", 20)
    first = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF)
    second = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF, offset=first["next_offset"])
    assert first["text"] == "[page 1]\nBefore impl" and second["offset"] == 20
    assert [c["url"] for c in fake_http.calls].count(RESPONSE_URL) == 1


def test_read_page_html_attachment_reads_its_gov_uk_path(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    html_path = (CONSULTATION + "/a-fairer-pathway-to-settlement-statement-and-accompanying-consultation-on-"
                 "earned-settlement-accessible")
    fake_http.add(CONTENT + html_path, json={"base_path": html_path, "title": "Accessible", "document_type":
                                             "html_publication", "details": {"body": "<p>Chapter 1</p>"}})
    page = govuk.read_page(CONSULTATION, attachment=html_path)
    assert page["id"] == html_path and page["text"] == "Chapter 1"


def test_read_page_unknown_attachment_lists_the_ids(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    with pytest.raises(LookupError, match=f"No attachment 'x.pdf'.*{RESPONSE_PDF}"):
        govuk.read_page(CONSULTATION, attachment="x.pdf")


def test_read_page_pdf_with_no_text_layer_is_an_error(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    fake_http.add(RESPONSE_URL, content=make_pdf(["", ""]))
    with pytest.raises(ValueError, match="has no text layer"):
        govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF)


def test_read_page_spreadsheet_attachment_points_to_preview_data(fake_http):
    fake_http.add(CONTENT + "/government/x", json={"base_path": "/government/x", "details": {"attachments": [
        {"attachment_type": "file", "filename": "t.ods", "url": "https://assets.publishing.service.gov.uk/1/t.ods"}]}})
    with pytest.raises(ValueError, match="use preview_data"):
        govuk.read_page("/government/x", attachment="t.ods")
    assert all("t.ods" not in c["url"] for c in fake_http.calls)


# Search inside a page or PDF with `find` (plan 005 D3 option 2).
FIND_PDF = make_pdf(["Intro line", "Some text here", "Before implementation, the Government will publish guidance.",
                     "Closing line"])


def test_find_returns_matching_lines_with_context_offset_and_page(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    fake_http.add(RESPONSE_URL, content=FIND_PDF)
    whole = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF)["text"]
    page = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF, find="before implementation")
    assert page["matches"] == [{
        "offset": whole.index("[page 3]"), "page": 3,
        "text": "[page 3]\nBefore implementation, the Government will publish guidance.\n[page 4]"}]
    assert page["match_count"] == 1 and page["truncated"] is False
    assert "text" not in page and "next_offset" not in page
    assert page["total_chars"] == len(whole)


def test_find_takes_a_regex_and_merges_neighbouring_matches(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    fake_http.add(RESPONSE_URL, content=FIND_PDF)
    page = govuk.read_page(CONSULTATION, attachment=RESPONSE_PDF, find=r"^(Intro|Some) \w+")
    assert page["match_count"] == 2
    assert page["matches"] == [{"offset": 0, "page": 1,
                                "text": "[page 1]\nIntro line\n[page 2]\nSome text here\n[page 3]"}]


def test_find_on_a_web_page_has_no_page_number(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    page = govuk.read_page(STATEMENT, find="Mr Speaker, with permission")
    assert page["matches"][0]["offset"] == 0 and page["matches"][0]["page"] is None
    assert page["matches"][0]["text"].startswith("Mr Speaker, with permission")


def test_find_with_no_match_returns_no_matches(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    page = govuk.read_page(STATEMENT, find="zzz no such words zzz")
    assert page["matches"] == [] and page["match_count"] == 0


def test_find_stops_at_max_chars_and_says_so(fake_http, monkeypatch):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    monkeypatch.setattr(govuk, "MAX_CHARS", 500)
    page = govuk.read_page(STATEMENT, find=".")
    assert page["truncated"] is True and page["match_count"] > len(page["matches"]) >= 1
    assert sum(len(m["text"]) for m in page["matches"]) == 500


def test_find_bad_regex_is_an_error(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    with pytest.raises(ValueError, match="not a valid regular expression"):
        govuk.read_page(STATEMENT, find="(unclosed")


def test_find_with_offset_is_an_error(fake_http):
    add_content(fake_http, STATEMENT, "content_earned-settlement-statement.json")
    with pytest.raises(ValueError, match="find searches the whole page"):
        govuk.read_page(STATEMENT, offset=10, find="x")


def test_find_on_an_html_attachment_searches_that_page(fake_http):
    add_content(fake_http, CONSULTATION, "content_earned-settlement-consultation.json")
    html_path = (CONSULTATION + "/a-fairer-pathway-to-settlement-statement-and-accompanying-consultation-on-"
                 "earned-settlement-accessible")
    fake_http.add(CONTENT + html_path, json={"base_path": html_path, "details": {"body": "<p>A</p><p>Chapter 1</p>"}})
    page = govuk.read_page(CONSULTATION, attachment=html_path, find="chapter")
    assert page["matches"] == [{"offset": 0, "page": None, "text": "A\nChapter 1"}]
