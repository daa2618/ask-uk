"""File links on a publisher's web page, from small inline HTML."""
from ukdatapy.core.links import file_links

PAGE = "https://www.ethnicity-facts-figures.service.gov.uk/work-pay-and-benefits/pay-and-income/household-income/latest/"


def test_relative_csv_link_is_resolved_and_named_from_its_text():
    html = b'<a href="downloads/by-ethnicity.csv">  Download\n  by ethnicity </a>'
    assert file_links(html, PAGE) == [{
        "name": "Download by ethnicity",
        "url": PAGE + "downloads/by-ethnicity.csv",
        "format": "CSV",
    }]


def test_xlsx_and_xls_case_insensitive_and_aria_label_wins():
    html = (b'<a href="https://assets.publishing.service.gov.uk/media/1/Fuel.XLSX" aria-label="Fuel poverty tables">'
            b'<span>xlsx</span></a><a href="/old.xls">Old</a>')
    out = file_links(html, "https://www.gov.uk/government/statistics/fuel-poverty-trends-2025")
    assert out == [
        {"name": "Fuel poverty tables", "url": "https://assets.publishing.service.gov.uk/media/1/Fuel.XLSX",
         "format": "XLSX"},
        {"name": "Old", "url": "https://www.gov.uk/old.xls", "format": "XLS"},
    ]


def test_skips_other_files_csv_preview_http_off_allowlist_and_duplicates():
    html = b"""
      <a href="https://assets.publishing.service.gov.uk/media/2/a.csv">A</a>
      <a href="https://www.gov.uk/csv-preview/2/a.csv">Preview A</a>
      <a href="https://assets.publishing.service.gov.uk/media/2/a.csv">A again</a>
      <a href="https://assets.publishing.service.gov.uk/media/2/report.pdf">PDF</a>
      <a href="http://www.gov.uk/plain.csv">Plain http</a>
      <a href="https://evil.example.com/b.csv">Evil</a>
      <a href="https://www.gov.uk/data.csv?x=1">Query</a>
      <a>No href</a>
    """
    out = file_links(html, "https://www.gov.uk/government/statistics/x")
    assert [link["url"] for link in out] == [
        "https://assets.publishing.service.gov.uk/media/2/a.csv",
        "https://www.gov.uk/data.csv?x=1",
    ]
    assert out[0]["name"] == "A"


def test_an_empty_thumbnail_link_takes_its_name_from_a_later_link_to_the_same_file():
    # GOV.UK puts an image-only link before the titled link to each attachment.
    url = "https://assets.publishing.service.gov.uk/media/1/Trends.xlsx"
    html = f'<a href="{url}"><img src="t.png"></a><a href="{url}">Fuel poverty trends</a>'.encode()
    assert file_links(html, "https://www.gov.uk/government/statistics/x")[0]["name"] == "Fuel poverty trends"


def test_page_without_file_links_is_empty():
    assert file_links(b"<html><body><a href='/about'>About</a></body></html>", PAGE) == []


def test_ods_link_is_kept():
    # DfT attaches its bus, rail and road tables to GOV.UK pages as ODS only.
    html = b'<a href="https://assets.publishing.service.gov.uk/media/3/bus06.ods">Vehicles operated</a>'
    out = file_links(html, "https://www.gov.uk/government/statistical-data-sets/bus-statistics-data-tables")
    assert out == [{"name": "Vehicles operated", "url": "https://assets.publishing.service.gov.uk/media/3/bus06.ods",
                    "format": "ODS"}]
