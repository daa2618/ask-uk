import io
import zipfile
from pathlib import Path

import openpyxl
import pytest

from ukdatapy.core import tabular

CSV = b"region,year,cars,cycles\nWales,2024,100,5\nWales,2025,110,7\nScotland,2025,90,9\nLondon,2025,300,1,000\n"

# Defra-style: a title line first, Latin-1 bytes, ":" for no data, numbers with thousands separators.
DEFRA = ("Fly-tipping incidents, England\n"
         "Year,LA Name,Tyres Incidents,Total Incidents\n"
         "2024-25,Bradford,348,\"12,001\"\n"
         "2024-25,Haringey,319,\"9,500\"\n"
         "2024-25,Fylde,:,:\n"
         "2023-24,Bradford,999,\"11,000\"\n"
         "2024-25,Köln test,10,20\n").encode("latin-1")


def xlsx_bytes():
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "Cover"
    cover["A1"] = "About this workbook"
    data = wb.create_sheet("Table 1")
    data.append(["Table 1: Population density"])
    data.append([])
    data.append(["Borough", "Year", "Density"])
    data.append(["Tower Hamlets", 2010, 12563.4])
    data.append(["Hackney", 2010, 11000.0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ODS_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"')

# Real ONS workbook, mid-year estimates of the very old (ukevo2025.xls): 7 sheets, title lines above the header.
XLS = (Path(__file__).parent / "fixtures" / "tabular" / "ukevo2025.xls").read_bytes()


def ods_bytes(table_1_rows=None):
    """An ODS file shaped like a DfT table: a cover sheet, then a title line, blank rows, the header, data rows,
    and the padding LibreOffice writes (empty columns and rows repeated to the sheet's edge)."""
    t1 = table_1_rows or (
        '<table:table-row><table:table-cell office:value-type="string"><text:p>Table BUS06b</text:p>'
        '</table:table-cell><table:table-cell table:number-columns-repeated="16380"/></table:table-row>'
        '<table:table-row table:number-rows-repeated="2"><table:table-cell/></table:table-row>'
        '<table:table-row>'
        '<table:table-cell office:value-type="string"><text:p>Year</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>London</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>Great<text:s text:c="2"/>Britain</text:p>'
        '</table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>Electric</text:p><text:p>share</text:p>'
        '</table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>Flag</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>Note</text:p></table:table-cell>'
        '<table:table-cell table:number-columns-repeated="16378"/></table:table-row>'
        # Stored values differ from the displayed text: 8.913 shows as "8.9", 0.33 shows as "33%". LibreOffice
        # writes the stored value with 17 significant digits, as in DfT's bus06.ods.
        '<table:table-row>'
        '<table:table-cell office:value-type="float" office:value="2025"><text:p>2025</text:p></table:table-cell>'
        '<table:table-cell office:value-type="float" office:value="8.9130000000000003"><text:p>8.9</text:p></table:table-cell>'
        '<table:table-cell office:value-type="float" office:value="35100"><text:p>35.1</text:p></table:table-cell>'
        '<table:table-cell office:value-type="percentage" office:value="0.33"><text:p>33%</text:p>'
        '</table:table-cell>'
        '<table:table-cell office:value-type="boolean" office:boolean-value="true"><text:p>TRUE</text:p>'
        '</table:table-cell>'
        '<table:table-cell office:value-type="date" office:date-value="2025-03-31"><text:p>31/03/25</text:p>'
        '</table:table-cell>'
        '</table:table-row>'
        # One cell repeated across three columns, then a merged cell that covers the next column.
        '<table:table-row>'
        '<table:table-cell office:value-type="float" office:value="2024"><text:p>2024</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string" table:number-columns-repeated="2"><text:p>[x]</text:p>'
        '</table:table-cell>'
        '<table:table-cell office:value-type="string" table:number-columns-spanned="2"><text:p>merged</text:p>'
        '</table:table-cell><table:covered-table-cell/>'
        '<table:table-cell office:value-type="string"><text:p>last</text:p></table:table-cell>'
        '</table:table-row>'
        '<table:table-row table:number-rows-repeated="1048570"><table:table-cell '
        'table:number-columns-repeated="16384"/></table:table-row>')
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ODS_NS}><office:body>'
               '<office:spreadsheet>'
               '<table:table table:name="Cover_Sheet"><table:table-row><table:table-cell office:value-type="string">'
               '<text:p>About this table</text:p></table:table-cell></table:table-row></table:table>'
               f'<table:table table:name="BUS06b">{t1}</table:table>'
               '</office:spreadsheet></office:body></office:document-content>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def test_csv_columns_rows_and_total():
    out = tabular.preview(CSV, "CSV", limit=2)
    assert out["columns"] == ["region", "year", "cars", "cycles"]
    assert out["total_rows"] == 4
    assert out["rows"] == [{"region": "Wales", "year": "2024", "cars": "100", "cycles": "5"},
                           {"region": "Wales", "year": "2025", "cars": "110", "cycles": "7"}]
    assert out["truncated"] is True


def test_where_filters_case_insensitively_and_accepts_a_list():
    out = tabular.preview(CSV, "CSV", where={"region": ["wales", "SCOTLAND"], "year": "2025"})
    assert [r["region"] for r in out["rows"]] == ["Wales", "Scotland"]
    assert out["total_rows"] == 2


def test_unknown_column_is_a_clear_error():
    with pytest.raises(ValueError, match="No column 'county'"):
        tabular.preview(CSV, "CSV", where={"county": "x"})


def test_title_row_is_skipped_and_latin1_decoded():
    out = tabular.preview(DEFRA, "CSV")
    assert out["columns"] == ["Year", "LA Name", "Tyres Incidents", "Total Incidents"]
    assert "Köln test" in [r["LA Name"] for r in out["rows"]]


def test_sort_is_numeric_with_no_data_markers_last():
    out = tabular.preview(DEFRA, "CSV", where={"Year": "2024-25"}, sort_by="Tyres Incidents", descending=True)
    assert [r["LA Name"] for r in out["rows"]] == ["Bradford", "Haringey", "Köln test", "Fylde"]


def test_numeric_sort_handles_thousands_separators():
    out = tabular.preview(DEFRA, "CSV", where={"Year": "2024-25"}, sort_by="Total Incidents", limit=1,
                          descending=True)
    assert out["rows"][0]["LA Name"] == "Bradford"


def test_columns_selects_and_orders():
    out = tabular.preview(CSV, "CSV", columns=["cycles", "region"], limit=1)
    assert out["rows"] == [{"cycles": "5", "region": "Wales"}]


def test_offset_pages_through():
    out = tabular.preview(CSV, "CSV", offset=3, limit=10)
    assert [r["region"] for r in out["rows"]] == ["London"]
    assert out["truncated"] is False


def test_limit_is_capped():
    assert tabular.preview(CSV, "CSV", limit=10_000)["limit"] == tabular.MAX_ROWS


def test_xlsx_lists_sheets_and_finds_the_header_row():
    out = tabular.preview(xlsx_bytes(), "XLSX", sheet="Table 1")
    assert out["sheets"] == ["Cover", "Table 1"]
    assert out["columns"] == ["Borough", "Year", "Density"]
    assert out["rows"][0] == {"Borough": "Tower Hamlets", "Year": "2010", "Density": "12563.4"}


def test_xlsx_defaults_to_the_first_sheet_and_unknown_sheet_errors():
    assert tabular.preview(xlsx_bytes(), "XLSX")["sheet"] == "Cover"
    with pytest.raises(ValueError, match="No sheet 'Nope'"):
        tabular.preview(xlsx_bytes(), "XLSX", sheet="Nope")


def test_format_is_sniffed_when_unknown():
    assert tabular.preview(xlsx_bytes(), "")["sheets"] == ["Cover", "Table 1"]
    assert tabular.preview(CSV, "")["columns"][0] == "region"


def test_ods_lists_sheets_and_finds_the_header_row():
    out = tabular.preview(ods_bytes(), "ODS", sheet="BUS06b")
    assert out["sheets"] == ["Cover_Sheet", "BUS06b"]
    assert out["columns"] == ["Year", "London", "Great Britain", "Electric share", "Flag", "Note"]
    assert out["total_rows"] == 2


def test_ods_reads_stored_values_not_the_displayed_text():
    row = tabular.preview(ods_bytes(), "ODS", sheet="BUS06b")["rows"][0]
    assert row == {"Year": "2025", "London": "8.913", "Great Britain": "35100", "Electric share": "0.33",
                   "Flag": "true", "Note": "2025-03-31"}


def test_ods_expands_repeated_and_merged_cells_in_place():
    row = tabular.preview(ods_bytes(), "ODS", sheet="BUS06b")["rows"][1]
    assert row == {"Year": "2024", "London": "[x]", "Great Britain": "[x]", "Electric share": "merged",
                   "Flag": "", "Note": "last"}


def test_ods_defaults_to_the_first_sheet_and_unknown_sheet_errors():
    assert tabular.preview(ods_bytes(), "ODS")["sheet"] == "Cover_Sheet"
    with pytest.raises(ValueError, match="No sheet 'Nope'"):
        tabular.preview(ods_bytes(), "ODS", sheet="Nope")


def test_ods_is_sniffed_from_its_mimetype_not_taken_for_xlsx():
    assert tabular.preview(ods_bytes(), "")["sheets"] == ["Cover_Sheet", "BUS06b"]


def test_ods_rows_repeated_past_the_sheet_limit_stop_at_it(monkeypatch):
    # A repeat count is an attribute, so a few bytes can claim a billion copies of a filled row.
    monkeypatch.setattr(tabular, "MAX_SHEET_ROWS", 1000)
    rows = ('<table:table-row><table:table-cell office:value-type="string"><text:p>a</text:p></table:table-cell>'
            '<table:table-cell office:value-type="string"><text:p>b</text:p></table:table-cell></table:table-row>'
            '<table:table-row table:number-rows-repeated="1000000000">'
            '<table:table-cell office:value-type="string"><text:p>x</text:p></table:table-cell>'
            '<table:table-cell office:value-type="string"><text:p>y</text:p></table:table-cell></table:table-row>')
    out = tabular.preview(ods_bytes(rows), "ODS", sheet="BUS06b", where={"a": "never"})
    assert out["total_rows"] == 0


def _xlsx_with_row_at(index):
    """One-sheet XLSX: a header in row 1 and one data row at `index`, with no <dimension> element, so openpyxl
    has no row count and yields an empty row for every index in the gap."""
    wb = openpyxl.Workbook()
    wb.active.append(["a"])
    wb.active.append(["x"])
    buf = io.BytesIO()
    wb.save(buf)
    src, out = zipfile.ZipFile(buf), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode()
                xml = xml[:xml.index("<dimension")] + xml[xml.index("/>", xml.index("<dimension")) + 2:]
                data = xml.replace('r="2"', f'r="{index}"').replace('r="A2"', f'r="A{index}"').encode()
            z.writestr(info, data)
    return out.getvalue()


def test_xlsx_rows_past_the_sheet_limit_stop_at_it(monkeypatch):
    # A row number is an attribute, so a 5 KB file can put its one data row at r=10**12; every gap row is read.
    monkeypatch.setattr(tabular, "MAX_SHEET_ROWS", 1000)
    assert tabular.preview(_xlsx_with_row_at(999), "XLSX")["total_rows"] == 1
    assert tabular.preview(_xlsx_with_row_at(1001), "XLSX")["total_rows"] == 0


def test_xls_lists_sheets_and_finds_the_header_row():
    out = tabular.preview(XLS, "XLS", sheet="UK Persons", where={"Year": "2002"})
    assert out["sheets"] == ["Contents", "Q & M", "T & C", "Notes", "UK Persons", "UK Men", "UK Women"]
    assert out["columns"][:3] == ["Year", "90 and over", "90 to 99"]
    # Whole numbers are stored as floats in XLS; they come back as "387938", like the XLSX path, not "387938.0".
    assert out["rows"][0]["90 and over"] == "387938"


def test_xls_defaults_to_the_first_sheet_and_unknown_sheet_errors():
    assert tabular.preview(XLS, "XLS")["sheet"] == "Contents"
    with pytest.raises(ValueError, match="No sheet 'Nope'"):
        tabular.preview(XLS, "XLS", sheet="Nope")


def test_xls_is_sniffed_when_unknown():
    assert tabular.preview(XLS, "")["sheet"] == "Contents"


def test_unsupported_format():
    with pytest.raises(ValueError, match="Cannot preview"):
        tabular.preview(b"%PDF-1.7", "PDF")


def test_rows_read_from_the_file_are_capped(monkeypatch):
    assert tabular.preview(CSV, "CSV")["source_rows_capped"] is False
    monkeypatch.setattr(tabular, "MAX_SOURCE_ROWS", 3)
    out = tabular.preview(CSV, "CSV")
    assert out["total_rows"] == 3  # the cap counts kept data rows; the header is not one of them
    assert out["source_rows_capped"] is True


def test_a_match_past_the_cap_is_still_found(monkeypatch):
    # Home Office Data_Vis_D02: 407,284 rows oldest first, so a cap applied before the filter dropped 2021 Q4 on.
    monkeypatch.setattr(tabular, "MAX_SOURCE_ROWS", 3)
    out = tabular.preview(CSV, "CSV", where={"region": "London"})
    assert [r["region"] for r in out["rows"]] == ["London"]
    assert out["source_rows_capped"] is False


def test_the_cap_counts_matching_rows(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_SOURCE_ROWS", 1)
    out = tabular.preview(CSV, "CSV", where={"year": "2025"})
    assert out["total_rows"] == 1 and out["source_rows_capped"] is True


def test_an_xlsx_match_past_the_cap_is_still_found(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_SOURCE_ROWS", 1)
    out = tabular.preview(xlsx_bytes(), "XLSX", sheet="Table 1", where={"Borough": "Hackney"})
    assert [r["Borough"] for r in out["rows"]] == ["Hackney"]
    assert out["source_rows_capped"] is False


def test_group_by_sums_a_column_and_counts_rows():
    out = tabular.preview(CSV, "CSV", group_by=["year"], sum="cars")
    assert out["columns"] == ["year", "cars", "row_count"]
    assert out["rows"] == [{"year": "2024", "cars": 100, "row_count": 1}, {"year": "2025", "cars": 500, "row_count": 3}]
    assert out["total_rows"] == 2 and out["sum_skipped"] == 0


def test_sum_without_group_by_is_one_total_row():
    out = tabular.preview(CSV, "CSV", where={"region": "Wales"}, sum="cars")
    assert out["rows"] == [{"cars": 210, "row_count": 2}]


def test_sum_skips_no_data_markers_and_counts_them():
    out = tabular.preview(DEFRA, "CSV", group_by=["Year"], sum="Total Incidents")
    assert out["rows"] == [{"Year": "2024-25", "Total Incidents": 21521, "row_count": 4},
                           {"Year": "2023-24", "Total Incidents": 11000, "row_count": 1}]
    assert out["sum_skipped"] == 1  # Fylde's ":"


def test_group_by_without_sum_counts_rows():
    out = tabular.preview(CSV, "CSV", where={"year": "2025"}, group_by=["region"])
    assert out["rows"] == [{"region": "Wales", "row_count": 1}, {"region": "Scotland", "row_count": 1},
                           {"region": "London", "row_count": 1}]
    assert "sum_skipped" not in out


def test_grouped_rows_sort_and_page_like_any_rows():
    out = tabular.preview(CSV, "CSV", group_by=["region"], sum="cars", sort_by="cars", descending=True, limit=1)
    assert out["rows"] == [{"region": "London", "cars": 300, "row_count": 1}]
    assert out["total_rows"] == 3 and out["truncated"] is True


def test_group_by_and_sum_columns_must_exist():
    with pytest.raises(ValueError, match="No column 'nope'"):
        tabular.preview(CSV, "CSV", group_by=["nope"])
    with pytest.raises(ValueError, match="No column 'nope'"):
        tabular.preview(CSV, "CSV", sum="nope")


def test_sort_by_after_grouping_must_be_a_grouped_column():
    with pytest.raises(ValueError, match="No column 'cycles'"):
        tabular.preview(CSV, "CSV", group_by=["region"], sum="cars", sort_by="cycles")


def test_a_total_over_no_matching_rows_is_zero():
    out = tabular.preview(CSV, "CSV", where={"region": "Mars"}, sum="cars")
    assert out["rows"] == [{"cars": 0, "row_count": 0}]


def test_the_total_covers_every_matching_row_not_one_page():
    big = ("n,value\n" + "".join(f"{i},1\n" for i in range(tabular.MAX_ROWS + 50))).encode()
    out = tabular.preview(big, "CSV", sum="value", limit=1)
    assert out["rows"] == [{"value": tabular.MAX_ROWS + 50, "row_count": tabular.MAX_ROWS + 50}]


# Plan 004: several sheets in one call. ukevo2025.xls has three sheets of the same table: UK Persons, UK Men, UK Women.
def test_sheets_stacks_rows_with_the_sheet_first():
    out = tabular.preview(XLS, "XLS", sheets=["UK Men", "UK Women"], where={"Year": "2002"})
    assert out["sheets_read"] == ["UK Men", "UK Women"]
    assert out["columns"][:2] == ["sheet", "Year"]
    assert [(r["sheet"], r["90 to 99"]) for r in out["rows"]] == [("UK Men", "88511"), ("UK Women", "291809")]


def test_header_whitespace_is_collapsed_so_sheets_line_up():
    # Persons heads its column "90 and over"; Men writes "90 \nand over". Both read as "90 and over".
    out = tabular.preview(XLS, "XLS", sheets=["UK Persons", "UK Men"], where={"Year": "2002"})
    assert "90 and over" in out["columns"] and "100 and over" in out["columns"]
    assert [r["90 and over"] for r in out["rows"]] == ["387938", "89328"]
    assert out["dropped_columns"] == {}


def two_sheet_xlsx():
    """Sheet A heads its name column "name"; sheet B, a later layout, calls it "area"."""
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "A"
    for row in (["name", "code", "cars"], ["Barnet", "E1", 140], ["Brent", "E2", 91]):
        a.append(row)
    b = wb.create_sheet("B")
    for row in (["area", "code", "cars"], ["Barnet", "E1", 139]):
        b.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_sheets_keeps_shared_columns_and_lists_the_dropped_ones():
    out = tabular.preview(two_sheet_xlsx(), "XLSX", sheets=["A", "B"])
    assert out["columns"] == ["sheet", "code", "cars"]
    assert out["dropped_columns"] == {"name": ["A"], "area": ["B"]}
    assert out["rows"][2] == {"sheet": "B", "code": "E1", "cars": "139"}


def test_sheets_group_and_sum_across_sheets():
    out = tabular.preview(XLS, "XLS", sheets=["UK Men", "UK Women"], where={"Year": "2002"},
                          group_by=["Year"], sum="90 to 99")
    assert out["rows"] == [{"Year": "2002", "90 to 99": 380320, "row_count": 2}]  # the UK Persons figure


def test_sheets_a_filter_column_missing_from_one_sheet_names_that_sheet():
    with pytest.raises(ValueError, match="No column 'name' in sheet 'B'"):
        tabular.preview(two_sheet_xlsx(), "XLSX", sheets=["A", "B"], where={"name": "Barnet"})


def test_sheets_unknown_sheet_errors():
    with pytest.raises(ValueError, match="No sheet 'Nope'"):
        tabular.preview(XLS, "XLS", sheets=["UK Men", "Nope"])


def test_sheet_and_sheets_together_error():
    with pytest.raises(ValueError, match="Pass sheet or sheets, not both"):
        tabular.preview(XLS, "XLS", sheet="UK Men", sheets=["UK Women"])


def test_sheets_on_a_csv_errors():
    with pytest.raises(ValueError, match="A CSV file has no sheets"):
        tabular.preview(CSV, "CSV", sheets=["x"])


def test_sheets_refuses_a_file_column_named_sheet():
    wb = openpyxl.Workbook()
    wb.active.title = "A"
    wb.active.append(["sheet", "value"])
    wb.active.append(["x", 1])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="already has a column named 'sheet'"):
        tabular.preview(buf.getvalue(), "XLSX", sheets=["A"])


def test_sheets_row_cap_counts_across_sheets(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_SOURCE_ROWS", 30)
    out = tabular.preview(XLS, "XLS", sheets=["UK Men", "UK Women"])  # 24 data rows each
    assert out["total_rows"] == 30
    assert out["source_rows_capped"] is True


def test_sheets_can_group_by_the_sheet_column():
    out = tabular.preview(XLS, "XLS", sheets=["UK Men", "UK Women"], where={"Year": "2002"},
                          group_by=["sheet"], sum="90 to 99")
    assert out["rows"] == [{"sheet": "UK Men", "90 to 99": 88511, "row_count": 1},
                           {"sheet": "UK Women", "90 to 99": 291809, "row_count": 1}]


def ods_with_external_link_cache():
    """ods_bytes plus the hidden cache a linked workbook leaves behind, in the shape of ORR's Table 1415
    (244 such tables there): a quoted file URL as the name, a table-source child, and one blank row."""
    z = zipfile.ZipFile(io.BytesIO(ods_bytes()))
    content = z.read("content.xml").decode()
    cache = ("<table:table table:name=\"'file:///U:/London/Work/Summary%201718.xlsx'#Cover\">"
             '<table:table-source xlink:href="file:///U:/London/Work/Summary%201718.xlsx" table:table-name="Cover" '
             'table:mode="copy-results-only"/><table:table-column/>'
             '<table:table-row table:number-rows-repeated="1048576"><table:table-cell '
             'table:number-columns-repeated="16384"/></table:table-row></table:table>')
    content = content.replace(ODS_NS, ODS_NS + ' xmlns:xlink="http://www.w3.org/1999/xlink"')
    content = content.replace("</office:spreadsheet>", cache + "</office:spreadsheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet",
                     compress_type=zipfile.ZIP_STORED)
        out.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def test_ods_hides_the_caches_of_linked_workbooks():
    data = ods_with_external_link_cache()
    assert tabular.preview(data, "ODS")["sheets"] == ["Cover_Sheet", "BUS06b"]
    assert tabular.preview(data, "ODS", sheet="BUS06b")["total_rows"] == 2
    with pytest.raises(ValueError, match=r"No sheet .*Sheets: \['Cover_Sheet', 'BUS06b'\]"):
        tabular.preview(data, "ODS", sheet="'file:///U:/London/Work/Summary%201718.xlsx'#Cover")


def xlsx_with_external_link():
    """xlsx_bytes plus a link to another workbook, in the parts Excel writes: xl/externalLinks/externalLink1.xml
    holding cached values of the linked file's sheets, its relationship to the file, and workbook.xml's
    externalReferences. The linked sheet names must not show up as sheets of this file."""
    src = zipfile.ZipFile(io.BytesIO(xlsx_bytes()))
    parts = {n: src.read(n) for n in src.namelist()}
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts["xl/externalLinks/externalLink1.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><externalLink xmlns="{main}" xmlns:r="{rel}">'
        '<externalBook r:id="rId1"><sheetNames><sheetName val="Linked Cover"/><sheetName val="Linked Data"/>'
        '</sheetNames><sheetDataSet><sheetData sheetId="1"><row r="1"><cell r="A1" t="str"><v>cached</v></cell>'
        '</row></sheetData></sheetDataSet></externalBook></externalLink>').encode()
    parts["xl/externalLinks/_rels/externalLink1.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
        f'Type="{rel}/externalLinkPath" Target="file:///U:/London/Work/Summary%201718.xlsx" '
        'TargetMode="External"/></Relationships>').encode()
    wb_rels = parts["xl/_rels/workbook.xml.rels"].decode()
    parts["xl/_rels/workbook.xml.rels"] = wb_rels.replace(
        "</Relationships>", f'<Relationship Id="rIdExt1" Type="{rel}/externalLink" '
        'Target="externalLinks/externalLink1.xml"/></Relationships>').encode()
    workbook = parts["xl/workbook.xml"].decode()
    parts["xl/workbook.xml"] = workbook.replace(
        "</sheets>", '</sheets><externalReferences><externalReference r:id="rIdExt1"/></externalReferences>').encode()
    types = parts["[Content_Types].xml"].decode()
    parts["[Content_Types].xml"] = types.replace("</Types>", (
        '<Override PartName="/xl/externalLinks/externalLink1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"/></Types>')).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name, body in parts.items():
            out.writestr(name, body)
    return buf.getvalue()


def test_xlsx_lists_only_its_own_sheets_when_it_links_to_another_workbook():
    data = xlsx_with_external_link()
    # Positive control: openpyxl does read the link, so the fixture is not silently ignored.
    assert len(openpyxl.load_workbook(io.BytesIO(data))._external_links) == 1
    assert tabular.preview(data, "XLSX")["sheets"] == ["Cover", "Table 1"]
    with pytest.raises(ValueError, match="No sheet 'Linked Data'"):
        tabular.preview(data, "XLSX", sheet="Linked Data")


# Home Office small boats style: one row per day, ISO datetimes, then summary rows that are not dates.
DAILY = (b"Date,Migrants arrived\n"
         b"2025-12-31T00:00:00,40\n"
         b"2026-01-01T00:00:00,10\n"
         b"2026-01-02 00:00:00,20\n"
         b"2026-03-15,30\n"
         b"2026-11-05T00:00:00,5\n"
         b"Change on year,-3\n")


def test_where_range_keeps_dates_between_inclusive_bounds_whatever_the_time_format():
    out = tabular.preview(DAILY, "CSV", where_range={"Date": ["2026-01-01", "2026-03-15"]})
    assert [r["Migrants arrived"] for r in out["rows"]] == ["10", "20", "30"]


def test_where_range_with_sum_gives_a_year_to_date_total():
    out = tabular.preview(DAILY, "CSV", where_range={"Date": ["2026-01-01", "2026-11-12"]}, sum="Migrants arrived")
    assert out["rows"] == [{"Migrants arrived": 65, "row_count": 4}]


def test_where_range_empty_bound_is_open():
    out = tabular.preview(DAILY, "CSV", where_range={"Date": ["", "2025-12-31"]})
    assert [r["Migrants arrived"] for r in out["rows"]] == ["40"]
    out = tabular.preview(DAILY, "CSV", where_range={"Date": ["2026-03-15", ""]})
    assert [r["Migrants arrived"] for r in out["rows"]] == ["30", "5"]


def test_where_range_compares_numbers_as_numbers():
    out = tabular.preview(DEFRA, "CSV", where_range={"Total Incidents": ["9500", "11,000"]})
    assert [r["LA Name"] for r in out["rows"]] == ["Haringey", "Bradford"]


def test_where_range_drops_cells_that_are_not_the_bounds_kind():
    # "Change on year" is not a date and ":" is not a number, so neither row can be in range.
    assert tabular.preview(DAILY, "CSV", where_range={"Date": ["2000-01-01", ""]})["total_rows"] == 5
    assert "Fylde" not in [r["LA Name"] for r in
                           tabular.preview(DEFRA, "CSV", where_range={"Tyres Incidents": ["0", ""]})["rows"]]


def test_where_range_combines_with_where():
    out = tabular.preview(CSV, "CSV", where={"region": "Wales"}, where_range={"year": ["2025", "2025"]})
    assert out["rows"] == [{"region": "Wales", "year": "2025", "cars": "110", "cycles": "7"}]


@pytest.mark.parametrize("bounds, message", [
    (["2026-01-01"], "two bounds"),
    (["", ""], "at least one bound"),
    (["2026-01-01", "2026-01-02", "2026-01-03"], "two bounds"),
    (["2020", "2026-01-01"], "both numbers or both dates"),
    (["January", "2026-01-01"], "not a number or an ISO date"),
    (["2026-13-01", ""], "not a number or an ISO date"),
])
def test_where_range_rejects_bad_bounds(bounds, message):
    with pytest.raises(ValueError, match=message):
        tabular.preview(DAILY, "CSV", where_range={"Date": bounds})


def test_where_range_column_must_exist():
    with pytest.raises(ValueError, match="No column 'Day'"):
        tabular.preview(DAILY, "CSV", where_range={"Day": ["2026-01-01", ""]})


# Audit leftovers 2026-11-13: zip members unpacked with no size limit, very wide rows kept in memory, and a
# negative ODS repeat count that lowered the row counter.

def _with_member(data, name, content):
    """The zip `data` with member `name` replaced by `content`."""
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info.filename, content if info.filename == name else src.read(info.filename))
    return out.getvalue()


def test_a_file_that_unpacks_past_the_limit_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_UNZIPPED_BYTES", 1000, raising=False)
    for fmt in ("ODS", ""):  # "" also covers the format sniff, which reads the mimetype member
        with pytest.raises(ValueError, match="unpacks to"):
            tabular.preview(ods_bytes(), fmt)


def test_a_huge_member_that_is_loaded_whole_is_refused():
    # openpyxl loads xl/styles.xml and xl/sharedStrings.xml whole even in read-only mode: 120 MB zips to about 120 KB.
    xlsx = xlsx_bytes()
    styles = zipfile.ZipFile(io.BytesIO(xlsx)).read("xl/styles.xml").decode()
    end = styles.rindex("</")
    data = _with_member(xlsx, "xl/styles.xml", (styles[:end] + " " * 120_000_000 + styles[end:]).encode())
    assert len(data) < 1_000_000
    with pytest.raises(ValueError, match="xl/styles.xml"):
        tabular.preview(data, "XLSX", sheet="Table 1")


def test_a_member_that_declares_less_than_it_holds_is_cut_off_with_an_error():
    # The size check trusts the sizes the zip declares; zipfile stops reading a member at that size.
    data = bytearray(xlsx_bytes())
    i = data.index(b"PK\x01\x02")
    while data[i + 46:i + 46 + len(b"xl/worksheets/sheet2.xml")] != b"xl/worksheets/sheet2.xml":
        i = data.index(b"PK\x01\x02", i + 4)
    data[i + 24:i + 28] = (10).to_bytes(4, "little")  # the central directory's uncompressed size
    with pytest.raises(zipfile.BadZipFile):
        tabular.preview(bytes(data), "XLSX", sheet="Table 1")


def test_cells_kept_are_capped_not_only_rows(monkeypatch):
    # A 100-column header and 50 rows: 5,000 cells. Rows are padded to the header's width.
    monkeypatch.setattr(tabular, "MAX_SOURCE_CELLS", 1000, raising=False)
    wide = (",".join(f"c{i}" for i in range(100)) + "\n" + "x\n" * 50).encode()
    out = tabular.preview(wide, "CSV")
    assert out["total_rows"] == 10
    assert out["source_rows_capped"] is True


def test_cells_cap_covers_a_tiny_ods_that_claims_wide_rows(monkeypatch):
    # A few hundred bytes: a 2,000-column header and one 2,000-column row repeated 1,000 times.
    monkeypatch.setattr(tabular, "MAX_SOURCE_CELLS", 100_000, raising=False)
    cell = '<table:table-cell office:value-type="string" table:number-columns-repeated="2000"><text:p>{}</text:p></table:table-cell>'
    rows = (f'<table:table-row>{cell.format("h")}</table:table-row>'
            f'<table:table-row table:number-rows-repeated="1000">{cell.format("x")}</table:table-row>')
    out = tabular.preview(ods_bytes(rows), "ODS", sheet="BUS06b")
    assert out["total_rows"] == 50
    assert out["source_rows_capped"] is True


def test_cells_cap_counts_across_stacked_sheets(monkeypatch):
    # Each row is kept with its sheet's 3 columns plus `sheet`: 4 cells. A has 2 rows and B 1, so a cap of 9 cells
    # keeps both rows of A and stops at B's row.
    monkeypatch.setattr(tabular, "MAX_SOURCE_CELLS", 9, raising=False)
    out = tabular.preview(two_sheet_xlsx(), "XLSX", sheets=["A", "B"])
    assert out["total_rows"] == 2 and out["source_rows_capped"] is True


def test_an_ods_negative_row_repeat_reads_the_row_once_and_keeps_the_sheet_limit(monkeypatch):
    # A negative repeat used to drop the row and lower the row counter, so the sheet limit stopped too late.
    monkeypatch.setattr(tabular, "MAX_SHEET_ROWS", 1000)
    one = '<table:table-row{}><table:table-cell office:value-type="string"><text:p>{}</text:p></table:table-cell></table:table-row>'
    rows = (one.format("", "a") + one.format(' table:number-rows-repeated="-2000000"', "x")
            + one.format(' table:number-rows-repeated="1500"', "y"))
    out = tabular.preview(ods_bytes(rows), "ODS", sheet="BUS06b", limit=1)
    assert out["rows"] == [{"a": "x"}]
    assert out["total_rows"] == 999  # 1,000 rows read: the header, "x" once, and 998 of "y"


def test_a_zip_resource_gets_the_format_error_not_the_size_check():
    # A zipped CSV over 100 MB is common; it is not a spreadsheet, so the spreadsheet size check does not apply.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.csv", b" " * 120_000_000)
    with pytest.raises(ValueError, match="Cannot preview a ZIP"):
        tabular.preview(buf.getvalue(), "ZIP")


def test_streamed_members_are_exempt_from_the_whole_load_limit(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_LOADED_MEMBER_BYTES", 100)  # below every content.xml and sheet here
    assert tabular.preview(ods_bytes(), "ODS", sheet="BUS06b")["total_rows"] > 0
    monkeypatch.setattr(tabular, "MAX_LOADED_MEMBER_BYTES", 10_000_000)  # styles and the rest stay under it
    assert tabular.preview(xlsx_bytes(), "XLSX", sheet="Table 1")["total_rows"] == 2


def test_a_part_named_like_a_sheet_but_declared_as_shared_strings_is_not_exempt():
    # openpyxl finds shared strings by the content type [Content_Types].xml declares, not by a fixed name, so a
    # file can put them under xl/worksheets/, which the whole-load limit skips because sheets are streamed.
    xlsx = xlsx_bytes()
    types = zipfile.ZipFile(io.BytesIO(xlsx)).read("[Content_Types].xml").decode()
    override = ('<Override PartName="/xl/worksheets/ss.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>')
    types = types.replace("</Types>", override + "</Types>")
    strings = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               + " " * 120_000_000 + "</sst>").encode()
    data = _with_member(xlsx, "[Content_Types].xml", types.encode())
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/ss.xml", strings)
    with pytest.raises(ValueError, match="xl/worksheets/ss.xml"):
        tabular.preview(buf.getvalue(), "XLSX", sheet="Table 1")
