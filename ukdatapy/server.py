"""ukdatapy MCP server: six tools over UK open data from the ONS, the London Datastore, data.gov.uk, GOV.UK and TfL.

Agents never pass a URL to preview_data. They pass a dataset and resource id, and the server takes the URL
from the publisher's catalogue, then checks it against the allowlist (ukdatapy/allowlist.toml).
"""
from datetime import date
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ukdatapy.core import audit, http, links, paths, tabular
from ukdatapy.sources import datagovuk, govuk, london, ons, tfl

SOURCES = {"ons": ons, "london": london, "datagovuk": datagovuk, "govuk": govuk, "tfl": tfl}
SourceName = Literal["ons", "london", "datagovuk", "govuk", "tfl"]
MAX_RESOURCES = 100  # describe_dataset lists at most this many files, newest first
PAGE_FORMATS = ("HTML", "WEBPAGE")  # catalogue resources that are a web page, not a file

mcp = FastMCP(
    "ukdatapy",
    instructions=(
        "UK open data from the Office for National Statistics (ons), the London Datastore (london), "
        "data.gov.uk (datagovuk) and GOV.UK statistics pages (govuk), where central government departments "
        "such as the Home Office publish their current tables, and Transport for London's published data files (tfl): station "
        "entries and exits and footfall, bus and rail demand, cycle hire journeys and cycle counts. Typical flow: search_datasets, then describe_dataset to list its files, "
        "then preview_data to read rows from one file. For an ONS time series (a page path containing "
        "/timeseries/, such as headline CPI, unemployment or GDP growth), use get_series instead. "
        "For what the government decided or announced, and when (ministers' statements, consultation outcomes, "
        "policy papers, changes to the Immigration Rules), use search_policy, then read_page. "
        "Try these tools before any other source. If no dataset answers the question, tell the user that ukdatapy "
        "found nothing and name what you searched, before using any other source."
    ),
)
mcp.add_middleware(audit.AuditMiddleware())  # one line per tool call in cache_dir()/calls_<YYYY-MM>.jsonl


def _aged(item):
    """The item with `age_days`: days since its `updated` date, or None when the catalogue gives no date."""
    try:
        age = (date.today() - date.fromisoformat(item.get("updated", "")[:10])).days
    except ValueError:
        age = None
    return {**item, "age_days": age}


def _describe(source, dataset_id):
    try:
        return SOURCES[source].describe(dataset_id)
    except LookupError as e:
        raise ToolError(str(e)) from e


@mcp.tool
def search_datasets(query: str, sources: list[SourceName] | None = None, limit: int = 5) -> dict:
    """Search dataset catalogues for `query`. Returns up to `limit` hits per source, each with its `source` and
    `id` for describe_dataset, and `age_days` since it was last updated. govuk returns up to twice `limit`: the
    best matches of any date and the best from the past year, newest first, each with its relevance `score` and
    `relevance_rank` (1 = best match). A source that fails is reported under `errors`; the others still answer.
    If no dataset answers the question, tell the user that ukdatapy found nothing and name what you searched,
    before using any other source."""
    results, errors = [], {}
    for name in sources or list(SOURCES):
        try:
            results += [_aged(h) for h in SOURCES[name].search(query, limit=max(1, min(limit, 20)))]
        except Exception as e:  # noqa: BLE001 -- one failing catalogue must not hide the others
            errors[name] = str(e)
    return {"results": results, "errors": errors}


@mcp.tool
def describe_dataset(source: SourceName, dataset_id: str) -> dict:
    """A dataset's title, description, publisher, last update and its files (`resources`), each with an `id`
    for preview_data and a `format`. The dataset and each file carry `age_days` since their last update.
    A dataset with more than 100 files lists the 100 newest and gives `resources_total`; older ids still work
    in preview_data."""
    meta = _describe(source, dataset_id)
    resources = meta["resources"]
    out = _aged(meta)
    if len(resources) > MAX_RESOURCES:
        resources = sorted(resources, key=lambda r: r.get("updated") or "", reverse=True)[:MAX_RESOURCES]
        out["resources_total"] = len(meta["resources"])
    return {**out, "resources": [_aged(r) for r in resources]}


@mcp.tool
def preview_data(source: SourceName, dataset_id: str, resource_id: str, sheet: str | None = None,
                 sheets: list[str] | None = None, columns: list[str] | None = None, where: dict[str, str | list[str]] | None = None,
                 where_range: dict[str, list[str]] | None = None, group_by: list[str] | None = None, sum: str | None = None,
                 sort_by: str | None = None, descending: bool = False, limit: int = 20, offset: int = 0) -> dict:
    """Rows from one CSV, XLSX, XLS or ODS file of a dataset. Call with no filters first to see `columns` (and
    `sheets` for a spreadsheet). Then narrow it: `where` keeps rows whose column equals a value or one of a list of values
    (case-insensitive), `where_range` keeps rows whose column lies in [from, to] (inclusive; both numbers or both
    ISO dates such as "2026-01-01"; "" for an open end; cells that are neither are left out), `sort_by` sorts numerically when the column holds numbers, `columns` picks columns,
    `limit` (max 200) and `offset` page through. `total_rows` counts the rows after filtering.
    To count or total, pass `group_by` (columns) and/or `sum` (a numeric column): you get one row per group with
    the total and its `row_count`, computed over every matching row in the file, not only one page. Cells that are
    not numbers are left out of the total and counted in `sum_skipped`.
    When a spreadsheet keeps one table per sheet (such as one year per sheet), pass `sheets` (a list of names)
    instead of `sheet` to read them all in one call: each row starts with a `sheet` column, only the columns every
    listed sheet has are kept, and `where`, `group_by` (including `sheet`), `sum` and `sort_by` work across them.
    `dropped_columns` lists each column left out and the sheets that have it; when a key column such as a name is
    there, the layout changed between sheets, so split the call where it changes.
    For a resource whose format is HTML or WEBPAGE, it returns the data `files` linked from that page instead;
    pass one of their ids back as `resource_id`."""
    meta = _describe(source, dataset_id)
    resources = {r["id"]: r for r in meta["resources"]}
    resource = resources.get(resource_id)
    page = resources.get(resource_id.partition("/")[0]) if resource is None else None
    if resource is None and (page is None or page["format"] not in PAGE_FORMATS):
        ids = [f"{r['id']} ({r['format'] or '?'})" for r in meta["resources"]]
        raise ToolError(f"No resource {resource_id!r} in {dataset_id!r}. Resources: {ids}")
    if source == "ons" and resource_id == "series_csv":
        raise ToolError("This is an ONS time series; call get_series with the dataset_id instead")
    try:
        if resource is not None and resource["format"] in PAGE_FORMATS:
            return {"resource": _aged(resource), "files": [_aged(f) for f in _page_files(resource)]}
        if resource is None:
            files = _page_files(page)
            resource = next((f for f in files if f["id"] == resource_id), None)
            if resource is None:
                raise ToolError(f"No file {resource_id!r} on page {page['id']!r}. Files: {[f['id'] for f in files]}")
        url = resource["url"]
        data = paths.load_daily_bytes(url, lambda: http.download(url))
        table = tabular.preview(data, resource["format"], sheet=sheet, sheets=sheets, columns=columns, where=where,
                                where_range=where_range, group_by=group_by, sum=sum, sort_by=sort_by, descending=descending, limit=limit, offset=offset)
    except http.BlockedURL as e:
        raise ToolError(f"{e}. ukdatapy only reads files from hosts on its allowlist") from e
    except (http.HttpError, ValueError) as e:
        fmt = (resource or page)["format"]
        raise ToolError(f"{e} (resource format: {fmt or 'unknown'})") from e
    return {"resource": _aged(resource), **table}


def _page_files(page):
    """The data files linked from a webpage resource. Each id is `<page id>/<file name>`, numbered on a clash."""
    html = http.get(page["url"], headers={"Accept": "text/html"}).content
    files, ids = [], set()
    for link in links.file_links(html, page["url"]):
        base = file_id = f"{page['id']}/{PurePosixPath(urlsplit(link['url']).path).name}"
        n = 1
        while file_id in ids:
            n += 1
            file_id = f"{base}-{n}"
        ids.add(file_id)
        files.append({"id": file_id, **link, "updated": page["updated"], "found_on": page["id"]})
    return files


@mcp.tool
def get_series(series: str, frequency: Literal["month", "quarter", "year"] | None = None,
               start_year: int | None = None, end_year: int | None = None, limit: int = 120) -> dict:
    """An ONS time series (headline figures such as CPI inflation, unemployment rate or GDP growth) by its page
    path or URL, e.g. /economy/inflationandpriceindices/timeseries/d7g7/mm23. Returns the series' title, CDID,
    unit, release date and observations, oldest first. `frequency` defaults to the most frequent one
    available. Only the latest `limit` observations are returned (max 500)."""
    try:
        data = SOURCES["ons"].get_series(series)
    except LookupError as e:
        raise ToolError(str(e)) from e
    except (http.BlockedURL, http.HttpError) as e:
        raise ToolError(str(e)) from e
    observations = data["observations"]
    available = [f for f in ("month", "quarter", "year") if any(o["frequency"] == f for o in observations)]
    frequency = frequency or (available[0] if available else "month")
    observations = [o for o in observations if o["frequency"] == frequency
                    and (start_year is None or int(o["period"][:4]) >= start_year)
                    and (end_year is None or int(o["period"][:4]) <= end_year)]
    limit = max(1, min(limit, 500))
    return {**{k: v for k, v in data.items() if k != "observations"}, "frequency": frequency,
            "frequencies": available, "observations": observations[-limit:],
            "truncated": len(observations) > limit}


@mcp.tool
def search_policy(query: str, limit: int = 5) -> dict:
    """Search GOV.UK policy pages for `query`: ministers' oral and written statements, speeches, consultations
    and their outcomes, policy papers (including statements of changes to the Immigration Rules), news, press
    releases, guidance and impact assessments. Each hit has its page `type`, `age_days`, and an `id` for
    read_page. Returns up to twice `limit`: the best matches of any date and the best from the past year,
    newest first, each with its relevance `score` and `relevance_rank` (1 = best match)."""
    try:
        hits = govuk.search_policy(query, limit=max(1, min(limit, 20)))
    except (http.BlockedURL, http.HttpError) as e:
        raise ToolError(str(e)) from e
    return {"results": [_aged(h) for h in hits]}


@mcp.tool
def read_page(path: str, offset: int = 0, attachment: str | None = None, find: str | None = None,
              source: SourceName = "govuk") -> dict:
    """The text of one GOV.UK page by its path, such as an id from search_policy, with its type, publisher,
    `published` and `updated` dates and its `attachments`. Returns up to 20,000 characters from `offset`;
    when `next_offset` is not null, call again with it to read on. An HTML attachment's `id` is a path to pass
    back to read_page. To read a PDF attachment, pass the page's `path` and the attachment's `id` as
    `attachment`: its text comes back the same way, each PDF page starting with a `[page N]` line
    (N counts PDF pages from 1, so it can differ from the page number printed on the page).
    To search instead of reading in order, pass `find`, a case-insensitive regular expression such as
    `implement|come into (force|effect)`: it returns `matches` from the whole text, each matching line with one
    line either side, its `offset` (pass it back to read on from there) and its PDF `page`. Use `find` first on
    a long PDF.
    To read a PDF file listed by describe_dataset in another source (such as a TfL user guide), pass that
    `source`, the dataset id as `path` and the file's resource id as `attachment`; `offset` and `find` work the same."""
    try:
        if source == "govuk":
            return govuk.read_page(path, offset=offset, attachment=attachment, find=find)
        return _read_pdf_file(source, path, attachment, offset, find)
    except http.BlockedURL as e:
        raise ToolError(f"{e}. ukdatapy only reads files from hosts on its allowlist") from e
    except (LookupError, ValueError, http.HttpError) as e:
        raise ToolError(str(e)) from e


def _read_pdf_file(source, dataset_id, resource_id, offset, find):
    if resource_id is None:
        raise ValueError(f"For source {source!r}, pass the dataset id as path and a PDF file id as attachment")
    meta = _describe(source, dataset_id)
    resource = next((r for r in meta["resources"] if r["id"] == resource_id), None)
    if resource is None:
        pdfs = [r["id"] for r in meta["resources"] if r["format"] == "PDF"]
        raise LookupError(f"No file {resource_id!r} in {dataset_id!r}. PDF files: {pdfs}")
    if resource["format"] != "PDF":
        raise ValueError(f"read_page reads PDF files, not {resource['format'] or 'this format'}; "
                         "for a CSV, XLSX, XLS or ODS file use preview_data")
    found = govuk.read_pdf(resource["url"], resource_id, offset=offset, find=find)
    return {"source": source, "id": meta["id"], "title": meta["title"], "publisher": meta["publisher"],
            "updated": meta["updated"], "url": meta["url"],
            "attachment": {"id": resource["id"], "title": resource["name"], "format": "PDF", "url": resource["url"]},
            **found}


def main():
    mcp.run(show_banner=False)
