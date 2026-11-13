"""A small, filtered slice of a CSV, XLSX, XLS or ODS file, sized for an agent's context.

Government files often start with title lines, so the header is the first row that fills at least
three quarters of the widest row. Values come back as the strings in the file; sorting parses numbers when it can.
"""
import csv
import io
import itertools
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

import openpyxl
import xlrd

MAX_ROWS = 200
# Data rows kept from one file or sheet after the `where` filter. Bounds memory for a large or zip-bomb file.
# Rows are filtered as they are read, so a match after row 300,000 is still found (plan 003 D2). Reading time is
# bounded by MAX_SHEET_ROWS for XLSX and ODS, and by http.MAX_BYTES for a CSV.
MAX_SOURCE_ROWS = 300_000
SCAN_ROWS = 50
# Rows read from one XLSX or ODS sheet: the most a real sheet can hold. Both formats let a few bytes claim a huge
# sheet (an ODS repeat count; an XLSX row number, which openpyxl fills with one empty row per missing index), so the
# read stops where a real sheet must end.
MAX_SHEET_ROWS = 1_048_576
MAX_COLUMNS = 16_384
# Cells kept from one file, summed over rows: a few bytes of CSV or ODS can claim rows 16,384 columns wide. The widest
# real result seen (2026-11-13, 38 cached files) kept 11.7 million cells.
MAX_SOURCE_CELLS = 30_000_000
# Unpacked size of an XLSX or ODS file, from the sizes the zip declares; zipfile stops each member at its declared
# size. Deflate packs about 1,000 to 1; the largest real file seen unpacked to 536 MB.
MAX_UNZIPPED_BYTES = 2_000_000_000
# Unpacked size of one member that openpyxl may load whole, such as xl/sharedStrings.xml. Sheets and ODS content.xml
# are streamed and exempt, unless [Content_Types].xml declares a sheet-named part as something else. The largest such member seen in a real file was 45 MB.
MAX_LOADED_MEMBER_BYTES = 100_000_000
ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
# A cell or bound that starts with YYYY-MM-DD is a date, whatever follows ("T00:00:00", " 00:00:00").
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def preview(data: bytes, fmt: str, *, sheet: str | None = None, sheets: list[str] | None = None,
            columns: list[str] | None = None,
            where: dict | None = None, where_range: dict | None = None, group_by: list[str] | None = None, sum: str | None = None,
            sort_by: str | None = None, descending: bool = False, limit: int = 20, offset: int = 0) -> dict:
    """Rows of one sheet after `where` and `where_range`. With `group_by` or `sum`, one row per group instead, holding the group's
    values, the total of the `sum` column and its `row_count` (plan 003 D3); cells that are not numbers, such as
    ":" for suppressed data, are left out of the total and counted in `sum_skipped`. `sort_by`, `columns`,
    `limit` and `offset` then apply to those rows.
    With `sheets`, the rows of each named sheet in turn, each starting with a `sheet` column, and only the columns
    every one of those sheets has (plan 004).
    `where_range` maps a column to [from, to]: inclusive bounds, both numbers or both ISO dates, "" for an open end.
    A cell that is not the bounds' kind, such as a "Change on year" row in a date column, is left out."""
    fmt = (fmt or "").upper()
    if data[:2] == b"PK" and fmt in ("", "XLSX", "ODS"):
        _check_unzipped_size(data)
    fmt = fmt or _sniff(data)
    if fmt not in ("CSV", "XLSX", "XLS", "ODS"):
        raise ValueError(f"Cannot preview a {fmt or 'file of unknown format'}; only CSV, XLSX, XLS and ODS are supported")
    if sheets is not None and sheet is not None:
        raise ValueError("Pass sheet or sheets, not both")
    if sheets is not None and fmt == "CSV":
        raise ValueError("A CSV file has no sheets")
    stacked = sheets is not None
    wanted = {name: {str(w).strip().casefold() for w in (values if isinstance(values, list) else [values])}
              for name, values in (where or {}).items()}
    ranges = _ranges(where_range)
    # `sheet` is our own column when stacking, so it can be filtered and grouped on like any other.
    needed = [n for n in [*(where or {}), *ranges, *(group_by or []), *([sum] if sum else [])] if not (stacked and n == "sheet")]
    names, header, records, capped, sheet_headers, cells = None, None, [], False, {}, 0
    for target in (sheets if stacked else [sheet]):
        names, target, rows = _open(data, fmt, target)
        try:
            head = list(itertools.islice(rows, SCAN_ROWS))
            header_at = _header_index(head)
            sheet_header = _names(head[header_at] if head else [])
            if stacked and "sheet" in sheet_header:
                raise ValueError(f"Sheet {target!r} already has a column named 'sheet'")
            for name in needed:
                if name not in sheet_header:
                    place = f" in sheet {target!r}" if stacked else ""
                    raise ValueError(f"No column {name!r}{place}. Columns: {sheet_header}")
            sheet_headers[target] = sheet_header
            header = sheet_header if header is None else [h for h in header if h in sheet_header]
            for row in itertools.chain(head[header_at + 1:], rows):
                if not any(row):
                    continue
                record = dict(zip(sheet_header, (row + [""] * len(sheet_header))[:len(sheet_header)]))
                if stacked:
                    record = {"sheet": target, **record}
                if (all(record[name].strip().casefold() in values for name, values in wanted.items())
                        and all(_in_range(record[name], *bounds) for name, bounds in ranges.items())):
                    if len(records) == MAX_SOURCE_ROWS or cells + len(record) > MAX_SOURCE_CELLS:
                        capped = True
                        break
                    records.append(record)
                    cells += len(record)
        finally:
            rows.close()
        if capped:
            break
    if stacked:
        dropped = {}
        for target, sheet_header in sheet_headers.items():
            for h in sheet_header:
                if h not in header:
                    dropped.setdefault(h, []).append(target)
        header = ["sheet", *header]
        records = [{h: r[h] for h in header} for r in records]
    else:
        sheet = target
    grouping = group_by is not None or sum is not None
    out_header = [*(group_by or []), *([sum] if sum else []), "row_count"] if grouping else header
    for name in [*([sort_by] if sort_by else []), *(columns or [])]:
        if name not in out_header:
            raise ValueError(f"No column {name!r}. Columns: {out_header}")

    skipped = 0
    if grouping:
        keys = group_by or []
        groups = {} if keys else {(): [0, 0]}
        for r in records:
            group = groups.setdefault(tuple(r[k] for k in keys), [0, 0])
            group[1] += 1
            if sum:
                value = _number(r[sum])
                if value is None:
                    skipped += 1
                else:
                    group[0] += value
        records = [{**dict(zip(keys, key)), **({sum: _tidy(total)} if sum else {}), "row_count": count}
                   for key, (total, count) in groups.items()]
        header = out_header

    if sort_by:
        numbers = [r for r in records if _number(r[sort_by]) is not None]
        others = [r for r in records if _number(r[sort_by]) is None]
        records = sorted(numbers, key=lambda r: _number(r[sort_by]), reverse=descending) + others
    if columns:
        records = [{c: r[c] for c in columns} for r in records]

    limit = max(1, min(limit, MAX_ROWS))
    page = records[offset:offset + limit]
    out = {"columns": columns or header, "total_rows": len(records), "offset": offset, "limit": limit,
           "rows": page, "truncated": offset + len(page) < len(records), "source_rows_capped": capped}
    if sum:
        out["sum_skipped"] = skipped
    if stacked:
        out = {"sheets": names, "sheets_read": sheets, "dropped_columns": dropped, **out}
    elif names is not None:
        out = {"sheets": names, "sheet": sheet, **out}
    return out


def _open(data, fmt, sheet):
    """(all sheet names, the sheet read, its rows). A CSV has no sheets, so its names are None."""
    if fmt == "CSV":
        return None, None, _csv_rows(data)
    return {"XLSX": _xlsx_rows, "XLS": _xls_rows, "ODS": _ods_rows}[fmt](data, sheet)


def _check_unzipped_size(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return  # not a zip after all; the reader for its format reports that
    members = z.infolist()
    # openpyxl finds shared strings and the workbook by the content type a file declares, not by a fixed name, so a
    # part under xl/worksheets/ is streamed only if the file declares no other type for it.
    retyped = set()
    types = next((m for m in members if m.filename == "[Content_Types].xml"), None)
    if types is not None and types.file_size <= MAX_LOADED_MEMBER_BYTES:  # a bigger one is refused below
        for part in ET.fromstring(z.read(types)).iter(_CONTENT_TYPES + "Override"):
            if not part.get("ContentType", "").endswith(".worksheet+xml"):
                retyped.add(part.get("PartName", "").lstrip("/"))
    total = 0
    for m in members:
        total += m.file_size
        streamed = m.filename == "content.xml" or (m.filename.startswith("xl/worksheets/") and m.filename not in retyped)
        if not streamed and m.file_size > MAX_LOADED_MEMBER_BYTES:
            raise ValueError(f"Refusing a file whose {m.filename} unpacks to {m.file_size:,} bytes; "
                             f"the limit is {MAX_LOADED_MEMBER_BYTES:,}")
    if total > MAX_UNZIPPED_BYTES:
        raise ValueError(f"Refusing a file that unpacks to {total:,} bytes; the limit is {MAX_UNZIPPED_BYTES:,}")


def _sniff(data):
    if data[:2] == b"PK":
        # ODS is a zip file too; its first entry, `mimetype`, says which.
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "ODS" if "mimetype" in z.namelist() and z.read("mimetype").strip() == ODS_MIMETYPE else "XLSX"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # the OLE2 container of a binary .xls
        return "XLS"
    head = data[:4096]
    if b"\x00" not in head and b"," in head:
        return "CSV"
    return ""


def _csv_rows(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return ([c.strip() for c in row] for row in csv.reader(io.StringIO(text)))


def _xlsx_rows(data, sheet):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    names = wb.sheetnames
    sheet = sheet or names[0]
    if sheet not in names:
        raise ValueError(f"No sheet {sheet!r}. Sheets: {names}")
    ws = wb[sheet]

    def rows():
        try:
            for row in itertools.islice(ws.iter_rows(values_only=True), MAX_SHEET_ROWS):
                yield ["" if v is None else str(v).strip() for v in row]
        finally:
            wb.close()
    return names, sheet, rows()


def _xls_rows(data, sheet):
    book = xlrd.open_workbook(file_contents=data, on_demand=True)
    names = book.sheet_names()
    sheet = sheet or names[0]
    if sheet not in names:
        book.release_resources()
        raise ValueError(f"No sheet {sheet!r}. Sheets: {names}")
    ws = book.sheet_by_name(sheet)

    def rows():
        try:
            for i in range(ws.nrows):
                yield [_xls_value(cell, book.datemode) for cell in ws.row(i)]
        finally:
            book.release_resources()
    return names, sheet, rows()


def _xls_value(cell, datemode):
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return str(_tidy(cell.value))  # XLS stores every number as a float; 2002.0 reads as "2002", as in XLSX
    if cell.ctype == xlrd.XL_CELL_DATE:
        return str(xlrd.xldate.xldate_as_datetime(cell.value, datemode))
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return str(bool(cell.value))
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value, "")
    return str(cell.value).strip()


def _ods_rows(data, sheet):
    """Rows of one ODS sheet, streamed from content.xml so a large file is never held as one tree."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names, linked = [], set()
    with z.open("content.xml") as f:
        for event, el in ET.iterparse(f, ("start", "end")):
            if event == "start" and el.tag == _TABLE + "table":
                names.append(el.get(_TABLE + "name"))
            elif event == "start" and el.tag == _TABLE + "table-source":
                # A hidden cached copy of a sheet in a linked workbook, not a sheet of this file: ORR's Table 1415
                # carries 244 of them next to its 4 real sheets. Spreadsheet programs do not show them.
                linked.add(names[-1])
            elif event == "end" and el.tag == _TABLE + "table-row":
                el.clear()
    names = [n for n in names if n not in linked]
    sheet = sheet or names[0]
    if sheet not in names:
        raise ValueError(f"No sheet {sheet!r}. Sheets: {names}")

    def rows():
        read, inside = 0, False
        with z.open("content.xml") as f:
            for event, el in ET.iterparse(f, ("start", "end")):
                if el.tag == _TABLE + "table":
                    if event == "start":
                        inside = el.get(_TABLE + "name") == sheet
                    elif inside:
                        return
                elif event == "end" and el.tag == _TABLE + "table-row":
                    if inside:
                        row = _ods_cells(el)
                        # Spreadsheet programs pad a sheet with one blank row repeated to the edge: read it once.
                        repeat = max(1, int(el.get(_TABLE + "number-rows-repeated", "1"))) if any(row) else 1
                        for _ in range(min(repeat, MAX_SHEET_ROWS - read)):
                            yield row
                        read += repeat
                        if read >= MAX_SHEET_ROWS:
                            return
                    el.clear()
    return names, sheet, rows()


def _ods_cells(row):
    """Cell values in column order. A repeated cell fills its columns; a merged cell's covered cells read as blank.
    Blank cells are only written out when a filled cell follows, so a row padded to 16,384 columns stays short."""
    cells, blanks = [], 0
    for cell in row:
        if cell.tag not in (_TABLE + "table-cell", _TABLE + "covered-table-cell"):
            continue
        repeat = int(cell.get(_TABLE + "number-columns-repeated", "1"))
        value = _ods_value(cell)
        if not value:
            blanks += repeat
            continue
        cells += [""] * min(blanks, MAX_COLUMNS - len(cells))
        cells += [value] * min(repeat, MAX_COLUMNS - len(cells))
        blanks = 0
    return cells


def _ods_value(cell):
    """The stored value, as XLSX gives it: 8946.2 and 0.33, not the "8.9" or "33%" a sheet displays."""
    kind = cell.get(_OFFICE + "value-type")
    if kind in ("float", "percentage", "currency"):
        # LibreOffice writes 17 significant digits ("8.9130000000000003"); the float's own text is "8.913".
        return str(_tidy(float(cell.get(_OFFICE + "value", "0"))))
    if kind in ("date", "time", "boolean"):
        return cell.get(_OFFICE + kind + "-value", "")
    return "\n".join(_ods_text(p) for p in cell.findall(_TEXT + "p")).strip()


def _ods_text(el):
    parts = [el.text or ""]
    for child in el:
        if child.tag == _TEXT + "s":
            parts.append(" " * min(int(child.get(_TEXT + "c", "1")), MAX_COLUMNS))
        elif child.tag == _TEXT + "tab":
            parts.append("\t")
        elif child.tag == _TEXT + "line-break":
            parts.append("\n")
        else:
            parts.append(_ods_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _header_index(rows):
    filled = [sum(1 for c in row if c) for row in rows[:SCAN_ROWS]]
    widest = max(filled, default=0)
    for i, count in enumerate(filled):
        if count >= max(2, -(-widest * 3 // 4)):
            return i
    return 0


def _names(row):
    names, seen = [], {}
    for i, cell in enumerate(row):
        # One space for every run of whitespace, so "90 \nand over" and "90 and over" name the same column.
        name = " ".join(cell.split()) or f"column_{i + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def _number(text):
    if isinstance(text, (int, float)):
        return float(text)
    try:
        return float(text.replace(",", "").replace("£", "").replace("%", "").strip())
    except ValueError:
        return None


def _range_value(text):
    """("date", date) or ("number", float) for a cell or bound, or None when it is neither."""
    text = str(text).strip()
    if _ISO_DATE.match(text):
        try:
            return "date", date.fromisoformat(text[:10])
        except ValueError:
            return None
    value = _number(text)
    return None if value is None else ("number", value)


def _ranges(where_range):
    """{column: (kind, low, high)}, with None for an open end. Bounds come from the caller, so check them here."""
    out = {}
    for name, bounds in (where_range or {}).items():
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f'where_range for {name!r} needs two bounds, [from, to]; use "" for an open end')
        parsed = []
        for bound in bounds:
            if str(bound).strip() == "":
                parsed.append(None)
                continue
            value = _range_value(bound)
            if value is None:
                raise ValueError(f"Bound {bound!r} for {name!r} is not a number or an ISO date (YYYY-MM-DD)")
            parsed.append(value)
        kinds = {p[0] for p in parsed if p}
        if not kinds:
            raise ValueError(f"where_range for {name!r} needs at least one bound")
        if len(kinds) > 1:
            raise ValueError(f"Bounds for {name!r} must be both numbers or both dates")
        out[name] = (kinds.pop(), *(p[1] if p else None for p in parsed))
    return out


def _in_range(text, kind, low, high):
    value = _range_value(text)
    if value is None or value[0] != kind:
        return False
    return (low is None or value[1] >= low) and (high is None or value[1] <= high)


def _tidy(total):
    return int(total) if float(total).is_integer() else total
