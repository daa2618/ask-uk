"""ONS source adapter: the ONS search API, ons.gov.uk page HTML and the time series CSV generator.

Ported from onspy (search.py `_query`/`_rows`, from_site.py `page_files`/`series_csv_url`/`_release_date`).
An ONS dataset id is a page path, such as /economy/inflationandpriceindices/timeseries/d7g7/mm23.
"""
import csv
import datetime
import io
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ukdatapy.core import http

SOURCE = "ons"
SEARCH_URL = "https://api.beta.ons.gov.uk/v1/search"
SITE_URL = "https://www.ons.gov.uk"
PUBLISHER = "Office for National Statistics"
LONDON = ZoneInfo("Europe/London")
FILE_EXTENSIONS = (".csv", ".xlsx", ".xls", ".ods")
FREQUENCIES = [("year", re.compile(r"\d{4}")), ("quarter", re.compile(r"\d{4} Q[1-4]")),
               ("month", re.compile(r"\d{4} [A-Z]{3}"))]


def search(query: str, limit: int = 10) -> list[dict]:
    # The API answers 400 "invalid characters in query" for £, € and é, so send ASCII only.
    params = {"q": " ".join(re.sub(r"[^\x00-\x7f]", " ", query).split()), "limit": str(limit)}
    body = http.get(SEARCH_URL, params=params).json()
    hits = []
    for item in body["items"][:limit]:
        if "uri" not in item:
            raise ValueError(f"Search result without a uri, the API may have changed: {item.get('title')!r}")
        parts = [item.get("type", "")]
        if item.get("cdid"):
            parts.append(f"CDID {item['cdid']}")
        if item.get("dataset_id"):
            parts.append(f"dataset {item['dataset_id']}")
        text = " ".join((item.get("summary") or item.get("meta_description") or "").split())
        if text:
            parts.append(text)
        summary = " | ".join(p for p in parts if p)
        hits.append({
            "source": SOURCE,
            "id": item["uri"],
            "title": item.get("title", ""),
            "summary": summary if len(summary) <= 300 else summary[:297] + "...",
            "url": SITE_URL + item["uri"],
            "updated": _london_date(item.get("release_date") or ""),
        })
    return hits


def describe(dataset_id: str) -> dict:
    path = _page_path(dataset_id)
    url = SITE_URL + path
    try:
        resp = http.get(url, headers={"Accept": "text/html"})
    except http.HttpError as exc:
        if exc.status == 404:
            raise LookupError(f"No ONS page at {url}") from exc
        raise
    soup = BeautifulSoup(resp.content, "html.parser")
    title = _title(soup)
    meta = soup.find("meta", attrs={"name": "description"})
    updated = _release_date(soup)
    if "/timeseries/" in path:
        resources = [{"id": "series_csv", "name": f"{title} (CSV)", "format": "CSV", "url": _series_csv_url(path),
                      "updated": updated}]
    else:
        resources = _page_resources(soup, url, path, updated)
    return {
        "source": SOURCE,
        "id": path,
        "title": title,
        "description": " ".join((meta.get("content", "") if meta else "").split())[:1000],
        "publisher": PUBLISHER,
        "updated": updated,
        "url": url,
        "resources": resources,
    }


def get_series(series: str) -> dict:
    """A time series page's CSV: its metadata and observations in file order (years, then quarters, then months)."""
    path = _page_path(series)
    if "/timeseries/" not in path:
        raise ValueError(f"Not an ONS time series page: {series}")
    text = http.download(_series_csv_url(path)).decode("utf-8-sig")
    # The "Important notes" field can pass the csv module's default 131072-character limit.
    csv.field_size_limit(max(csv.field_size_limit(), 2**31 - 1))
    meta, observations = {}, []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        key, value = row[0].strip(), row[1].strip()
        frequency = next((name for name, pattern in FREQUENCIES if pattern.fullmatch(key)), None)
        if frequency:
            observations.append({"period": key, "value": float(value) if value else None, "frequency": frequency})
        else:
            meta[key] = value
    return {
        "title": meta.get("Title", ""),
        "cdid": meta.get("CDID", ""),
        "unit": meta.get("Unit", ""),
        "release_date": _iso(meta.get("Release date", ""), "%d-%m-%Y"),
        "next_release": _iso(meta.get("Next release", ""), "%d %B %Y"),
        "dataset": meta.get("Source dataset ID", ""),
        "url": SITE_URL + path,
        "observations": observations,
    }


def _page_path(dataset_id):
    """The page path of an ONS id or www.ons.gov.uk URL. LookupError for anything else, before any request."""
    value = dataset_id.strip()
    if value.startswith(("http://", "https://")):
        parts = urlsplit(value)
        if parts.hostname not in ("www.ons.gov.uk", "ons.gov.uk"):
            raise LookupError(f"Not an ons.gov.uk page: {dataset_id}")
        value = parts.path
    path = value.rstrip("/")
    if not path.startswith("/"):
        raise LookupError(f"Not an ONS page path: {dataset_id!r}")
    return path


def _series_csv_url(path):
    # A literal string: passing uri= through params would percent-encode its slashes. Quote the rest, so a
    # path cannot add query parameters of its own.
    return f"{SITE_URL}/generator?format=csv&uri={quote(path, safe='/')}"


def _page_resources(soup, url, path, updated):
    """Downloadable files, then linked dataset pages, de-duplicated by URL.

    A dataset page's own sub-pages are its editions and are skipped. A topic page keeps its datasets under its own
    path, so those are listed.
    """
    own = path if "/datasets/" in path else None
    files, pages, seen, ids = [], [], set(), set()
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"])
        if link in seen:
            continue
        parts = urlsplit(link)
        name = " ".join((a.get("aria-label") or a.text).split())
        if parts.netloc == "www.ons.gov.uk" and parts.path == "/file":
            file_path = parse_qs(parts.query).get("uri", [""])[0]
            target = files
        elif parts.scheme == "https" and parts.path.lower().endswith(FILE_EXTENSIONS):
            file_path = parts.path
            target = files
        elif (parts.netloc == "www.ons.gov.uk" and "/datasets/" in parts.path
              and parts.path.rstrip("/") != path and not (own and parts.path.startswith(own + "/"))):
            file_path = None
            target = pages
        else:
            continue
        seen.add(link)
        if file_path is None:
            base, fmt, when = PurePosixPath(parts.path).name, "PAGE", ""
        else:
            base = PurePosixPath(file_path).name
            fmt, when = PurePosixPath(base).suffix.lstrip(".").upper(), updated
        target.append({"id": base, "name": name, "format": fmt, "url": link, "updated": when})
    resources = files + pages
    for resource in resources:
        base, n = resource["id"], 1
        while resource["id"] in ids:
            n += 1
            resource["id"] = f"{base}-{n}"
        ids.add(resource["id"])
    return resources


def _title(soup):
    h1 = soup.find("h1")
    if h1 is None:
        return ""
    label = h1.find("span", class_="page-intro__type")  # "Dataset", "Statistical bulletin" and so on
    if label:
        label.extract()
    return " ".join(h1.text.split())


def _release_date(soup):
    """The page's own "Release date:" as YYYY-MM-DD, or "". Dataset pages and time series pages mark it up
    differently, but both inside a .meta__item element; listings of other pages do not use that class."""
    for item in soup.find_all(class_="meta__item"):
        found = re.search(r"Release date:\s*(\d{1,2} [A-Za-z]+ \d{4})", " ".join(item.text.split()))
        if found:
            iso = _iso(found.group(1), "%d %B %Y")
            return iso if iso != found.group(1) else ""  # the contract allows only YYYY-MM-DD or ""
    return ""


def _iso(text, fmt):
    """text parsed with fmt as YYYY-MM-DD. Text that does not parse, such as "To be announced", comes back as is."""
    try:
        return datetime.datetime.strptime(text, fmt).date().isoformat()
    except ValueError:
        return text


def _london_date(stamp):
    """The API's UTC stamp as the London date: 2026-10-20T23:00:00.000Z is 21 October (BST)."""
    if not stamp:
        return ""
    return datetime.datetime.fromisoformat(stamp).astimezone(LONDON).date().isoformat()
