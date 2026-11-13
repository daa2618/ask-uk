"""GOV.UK source: statistics pages on www.gov.uk, through the public Search API and Content API.

Central government departments publish their statistics tables as attachments to GOV.UK pages, and often keep
the matching data.gov.uk entry years out of date. A dataset id is the page's path, such as
/government/statistical-data-sets/immigration-system-statistics-data-tables; its attachments are the resources.
"""
import re
from datetime import date, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ukdatapy.core import http, paths

SOURCE = "govuk"
SITE = "https://www.gov.uk"
SEARCH_URL = f"{SITE}/api/search.json"
CONTENT_URL = f"{SITE}/api/content"
FIELDS = ["title", "link", "description", "public_timestamp", "organisations", "content_store_document_type"]
DOCUMENT_TYPES = ["statistical_data_set", "official_statistics", "national_statistics"]
POLICY_TYPES = ["oral_statement", "written_statement", "speech", "open_consultation", "closed_consultation",
                "consultation_outcome", "policy_paper", "news_story", "press_release", "guidance", "detailed_guide",
                "statutory_guidance", "impact_assessment", "government_response"]
MAX_CHARS = 20_000  # characters of page text per read_page call
BLOCKS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "blockquote", "div"]


def _date(value):
    return (value or "")[:10]


def _search(query, limit, types):
    """Up to `limit` best matches of any date plus up to `limit` best matches from the past year, newest first.

    Relevance alone puts old editions first (a 2018 house building release above the 2026 ones), and newest
    first alone matches any page with any word of the query. So each hit keeps its relevance `score` and its
    `relevance_rank` (1 = best match) across both searches, and an old page that matches better still shows.
    """
    params = {"q": query, "count": limit, "fields": FIELDS, "filter_content_store_document_type": types}
    since = (date.today() - timedelta(days=365)).isoformat()
    results = {}
    for extra in ({}, {"filter_public_timestamp": f"from:{since}"}):
        for r in http.get(SEARCH_URL, params={**params, **extra}).json()["results"]:
            results.setdefault(r["link"], r)
    hits = []
    for r in results.values():
        publisher = ", ".join(o["title"] for o in r.get("organisations") or [] if o.get("title"))
        text = " ".join((r.get("description") or "").split())
        hits.append({
            "source": SOURCE,
            "id": r["link"],
            "title": r.get("title") or r["link"],
            "summary": (f"{publisher}: {text}" if publisher else text)[:300],
            "url": SITE + r["link"],
            "updated": _date(r.get("public_timestamp")),
            "score": r.get("es_score"),
            "type": r.get("content_store_document_type") or "",
        })
    for rank, h in enumerate(sorted(hits, key=lambda h: h["score"] or 0, reverse=True), 1):
        h["relevance_rank"] = rank
    return sorted(hits, key=lambda h: h["updated"], reverse=True)


def search(query: str, limit: int = 10) -> list[dict]:
    """Statistics pages: the source contract's search."""
    return [{k: v for k, v in h.items() if k != "type"} for h in _search(query, limit, DOCUMENT_TYPES)]


def search_policy(query: str, limit: int = 10) -> list[dict]:
    """Policy pages, such as ministers' statements, consultations and policy papers. Each hit carries its `type`."""
    return _search(query, limit, POLICY_TYPES)


def _path(dataset_id):
    """The page path for a dataset id. Only a path on www.gov.uk is accepted, never a URL or a query."""
    if "://" in dataset_id or dataset_id.startswith("//") or "?" in dataset_id or "#" in dataset_id \
            or not dataset_id.strip("/"):
        raise LookupError(f"Not a GOV.UK page path: {dataset_id!r}. Pass an id from search_datasets, "
                          "such as /government/statistics/...")
    return "/" + dataset_id.lstrip("/")


def _resource(attachment, updated):
    url = attachment.get("url") or ""
    if url.startswith("/"):
        url = SITE + url  # HTML attachments are other GOV.UK pages, given as a path
    name = PurePosixPath(urlsplit(url).path).name
    if attachment.get("attachment_type") == "html":
        fmt = "HTML"
    else:
        fmt = PurePosixPath(attachment.get("filename") or name).suffix.lstrip(".").upper()
    if not fmt and attachment.get("attachment_type") == "external":
        fmt = "HTML"  # a link to another site's page, such as the ORR data portal, not to a file
    return {
        "id": attachment.get("filename") or name or str(attachment.get("id") or ""),
        "name": attachment.get("title") or "",
        "format": fmt,
        "url": url,
        "updated": updated,
    }


def _content(path):
    try:
        return http.get(CONTENT_URL + path).json()
    except http.HttpError as err:
        if err.status in (404, 410):
            raise LookupError(f"No GOV.UK page {path!r}") from err
        raise


def describe(dataset_id: str) -> dict:
    path = _path(dataset_id)
    page = _content(path)
    updated = _date(page.get("public_updated_at"))
    organisations = (page.get("links") or {}).get("organisations") or []
    return {
        "source": SOURCE,
        "id": page.get("base_path") or path,
        "title": page.get("title") or path,
        "description": " ".join((page.get("description") or "").split())[:1000],
        "publisher": ", ".join(o["title"] for o in organisations if o.get("title")),
        "updated": updated,
        "url": SITE + (page.get("base_path") or path),
        "resources": _unique([_resource(a, updated) for a in (page.get("details") or {}).get("attachments") or []]),
    }


def _unique(resources):
    """Number an id that repeats (two attachments can share a file name), as server._page_files does."""
    seen = set()
    for r in resources:
        base, n = r["id"], 1
        while r["id"] in seen:
            n += 1
            r["id"] = f"{base}-{n}"
        seen.add(r["id"])
    return resources


def _text(html):
    """Plain text from GOV.UK body HTML: one line per paragraph, heading, list item or table row."""
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.find_all(BLOCKS):
        if el.name == "li":
            el.insert(0, "- ")
        el.insert_before("\n")
        el.insert_after("\n")
    lines = (" ".join(line.split()) for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def _pdf_text(data, name):
    """The text of a PDF, each page starting with a `[page N]` line."""
    try:
        pages = [page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages]
    except PdfReadError as err:
        raise ValueError(f"Cannot read PDF {name!r}: {err}") from err
    if not any(t.strip() for t in pages):
        raise ValueError(f"PDF {name!r} has no text layer; it may be a scan")
    parts = []
    for n, t in enumerate(pages, 1):
        lines = [" ".join(line.split()) for line in t.splitlines() if line.strip()]
        parts.append("\n".join([f"[page {n}]", *lines]))
    return "\n".join(parts)


def _find(text, pattern):
    """The lines of `text` that match `pattern`, each with one line either side; overlapping windows merge.

    Returns (matches, match_count, truncated). Each match has its character `offset` in `text`, the PDF `page`
    it starts on (None for a web page) and its `text`. The matches' text stops at MAX_CHARS in all.
    """
    lines = text.split("\n")
    starts, pages, at, page = [], [], 0, None
    for line in lines:
        m = re.fullmatch(r"\[page (\d+)\]", line)
        page = int(m.group(1)) if m else page
        starts.append(at)
        pages.append(page)
        at += len(line) + 1
    hits = [i for i, line in enumerate(lines) if pattern.search(line)]
    windows = []
    for i in hits:
        lo, hi = max(0, i - 1), min(len(lines) - 1, i + 1)
        if windows and lo <= windows[-1][1] + 1:
            windows[-1][1] = hi
        else:
            windows.append([lo, hi])
    matches, used, truncated = [], 0, False
    for lo, hi in windows:
        block = "\n".join(lines[lo:hi + 1])
        if used + len(block) > MAX_CHARS:
            block, truncated = block[:MAX_CHARS - used], True
        if block:
            matches.append({"offset": starts[lo], "page": pages[lo], "text": block})
        used += len(block)
        if truncated:
            break
    return matches, len(hits), truncated


def read_page(path: str, offset: int = 0, attachment: str | None = None, find: str | None = None) -> dict:
    """A GOV.UK page as plain text, MAX_CHARS characters from `offset`, plus its attachments.

    The text is the page body, followed by the outcome for a consultation. With `attachment` (an id from the
    page's attachments), the text is that attachment's instead: a PDF read from the page's own attachment URL,
    or an HTML attachment's GOV.UK page. With `find` (a regular expression, case-insensitive), the result
    has `matches` from the whole text instead of `text`: see _find.
    """
    pattern = _pattern(find, offset)
    path = _path(path)
    page = _content(path)
    details = page.get("details") or {}
    updated = _date(page.get("public_updated_at"))
    attachments = []
    for a in details.get("attachments") or []:
        if not isinstance(a, dict):
            continue
        r = _resource(a, updated)
        if r["format"] == "HTML":
            r["id"] = urlsplit(r["url"]).path
        attachments.append({"id": r["id"], "title": " ".join(r["name"].split()), "format": r["format"], "url": r["url"]})
    extra = {}
    if attachment is None:
        text = "\n".join(t for t in (_text(details.get("body")), _text(details.get("final_outcome_detail"))) if t)
    else:
        chosen = next((a for a in attachments if a["id"] == attachment), None)
        if chosen is None:
            raise LookupError(f"No attachment {attachment!r} on {path!r}. Attachments: {[a['id'] for a in attachments]}")
        if chosen["format"] == "HTML":
            return read_page(chosen["id"], offset=offset, find=find)
        if chosen["format"] != "PDF":
            raise ValueError(f"read_page reads PDF and HTML attachments, not {chosen['format'] or 'this format'}; "
                             "for a CSV, XLSX, XLS or ODS file use preview_data")
        url = chosen["url"]
        text = _pdf_text(paths.load_daily_bytes(url, lambda: http.download(url)), attachment)
        extra = {"attachment": chosen}
    found = _window(text, offset, pattern)
    organisations = (page.get("links") or {}).get("organisations") or []
    return {
        "source": SOURCE,
        "id": page.get("base_path") or path,
        "title": page.get("title") or path,
        "type": page.get("document_type") or "",
        "publisher": ", ".join(o["title"] for o in organisations if o.get("title")),
        "published": _date(page.get("first_published_at")),
        "updated": updated,
        "url": SITE + (page.get("base_path") or path),
        **extra,
        **found,
        "attachments": attachments,
    }


def _pattern(find, offset):
    """`find` compiled, case-insensitive, or None. Checked before any request."""
    if find is None:
        return None
    if offset:
        raise ValueError("find searches the whole page; leave offset at 0")
    try:
        return re.compile(find, re.IGNORECASE)
    except re.error as err:
        raise ValueError(f"find {find!r} is not a valid regular expression: {err}") from err


def _window(text, offset, pattern):
    """MAX_CHARS of `text` from `offset` with `next_offset`, or the `matches` of `pattern`; plus `total_chars`."""
    if offset < 0 or (offset and offset >= len(text)):
        raise ValueError(f"offset {offset} is past the end of the page ({len(text)} characters)")
    end = offset + MAX_CHARS
    if pattern is None:
        found = {"text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None}
    else:
        matches, count, truncated = _find(text, pattern)
        found = {"matches": matches, "match_count": count, "truncated": truncated}
    return {**found, "total_chars": len(text)}


def read_pdf(url: str, name: str, offset: int = 0, find: str | None = None) -> dict:
    """A PDF file from any source's catalogue, read and paged like a read_page attachment."""
    pattern = _pattern(find, offset)
    return _window(_pdf_text(paths.load_daily_bytes(url, lambda: http.download(url)), name), offset, pattern)
