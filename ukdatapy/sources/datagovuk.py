"""data.gov.uk source: the CKAN API behind data.gov.uk.

data.gov.uk/api/3/action answers 301 to www.data.gov.uk, which answers 301 to
ckan.publishing.service.gov.uk. Calling that host directly saves two requests per call.
"""
import html
import re

from ukdatapy.core import http

SOURCE = "datagovuk"
API = "https://ckan.publishing.service.gov.uk/api/3/action"
SITE = "https://www.data.gov.uk/dataset"


def _text(notes, limit):
    """CKAN notes can hold HTML; keep the plain text, whitespace collapsed, cut to limit characters."""
    plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", notes or "")).split())
    return plain[:limit]


def _publisher(pkg):
    return (pkg.get("organization") or {}).get("title") or ""


def _url(pkg):
    return f"{SITE}/{pkg['id']}/{pkg['name']}"


def _date(value):
    return (value or "")[:10]


def _call(action, params):
    body = http.get(f"{API}/{action}", params=params).json()
    if not body.get("success"):
        raise LookupError(f"data.gov.uk {action} {params}: {body.get('error')}")
    return body["result"]


def search(query: str, limit: int = 10) -> list[dict]:
    packages = _call("package_search", {"q": query, "rows": limit})["results"]
    hits = []
    for pkg in packages:
        publisher = _publisher(pkg)
        notes = _text(pkg.get("notes"), 300)
        hits.append({
            "source": SOURCE,
            "id": pkg["name"],
            "title": pkg.get("title") or pkg["name"],
            "summary": (f"{publisher}: {notes}" if publisher else notes)[:300],
            "url": _url(pkg),
            "updated": _date(pkg.get("metadata_modified")),
        })
    return hits


def describe(dataset_id: str) -> dict:
    try:
        pkg = _call("package_show", {"id": dataset_id})
    except http.HttpError as err:
        # CKAN answers 404 for an unknown id.
        if err.status == 404:
            raise LookupError(f"No data.gov.uk dataset {dataset_id!r}") from err
        raise
    return {
        "source": SOURCE,
        "id": pkg["name"],
        "title": pkg.get("title") or pkg["name"],
        "description": _text(pkg.get("notes"), 1000),
        "publisher": _publisher(pkg),
        "updated": _date(pkg.get("metadata_modified")),
        "url": _url(pkg),
        "resources": [
            {
                "id": res["id"],
                "name": res.get("name") or "",
                "format": (res.get("format") or "").strip().lstrip(".").upper(),
                "url": res.get("url") or "",
                "updated": _date(res.get("last_modified") or res.get("created")),
            }
            for res in pkg.get("resources", [])
        ],
    }
