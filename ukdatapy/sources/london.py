"""London Datastore source adapter, ported from london_data_store.

The whole catalogue is one export JSON. It is fetched once a day and cached with only the fields used here.
"""
import html
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from ukdatapy.core import http
from ukdatapy.core.paths import load_daily_json

SOURCE = "london"
EXPORT_URL = "https://data.london.gov.uk/api/v2/datasets/export.json"
# The export labels these by kind, not by file type; the URL extension says XLSX, DOCX and so on.
GENERIC_FORMATS = {"", "spreadsheet", "document", "slideshow", "image"}
WEIGHTS = (("title", 3), ("tags", 2), ("description", 1))


def _text(markup: str | None) -> str:
    """HTML to one line of plain text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup or ""))).strip()


def _format(resource: dict) -> str:
    fmt = (resource.get("format") or "").lower()
    if fmt in GENERIC_FORMATS:
        fmt = PurePosixPath(unquote(urlsplit(resource.get("url") or "").path)).suffix.lstrip(".") or fmt
    return fmt.upper()


def _slim(dataset: dict) -> dict:
    return {
        "id": dataset["id"],
        "title": (dataset.get("title") or "").strip(),
        "description": _text(dataset.get("description")),
        "tags": dataset.get("tags") or [],
        "publisher": dataset.get("publisher") or "",
        "updated": (dataset.get("updatedAt") or "")[:10],
        "url": dataset.get("webpage") or f"https://data.london.gov.uk/dataset/{dataset['id']}",
        "resources": [
            {"id": key, "name": r.get("title") or "", "format": _format(r), "url": r.get("url") or "",
             "updated": (r.get("check_timestamp") or "")[:10]}
            for key, r in (dataset.get("resources") or {}).items()
        ],
    }


def _catalogue() -> list[dict]:
    return load_daily_json("london_catalogue", lambda: [_slim(d) for d in http.get(EXPORT_URL, timeout=120).json()])


def _score(dataset: dict, words: list[str]) -> int:
    fields = {"title": dataset["title"], "tags": " ".join(dataset["tags"]), "description": dataset["description"]}
    # A word matches at the start of a word in the field: "price" finds "prices" and the tag "house-prices".
    return sum(weight for word in words for name, weight in WEIGHTS
               if re.search(r"\b" + re.escape(word), fields[name], re.IGNORECASE))


def search(query: str, limit: int = 10) -> list[dict]:
    words = re.findall(r"\w+", query.lower())
    scored = [(_score(d, words), d) for d in _catalogue()]
    scored = [(s, d) for s, d in scored if s > 0]
    scored.sort(key=lambda sd: (sd[0], sd[1]["updated"]), reverse=True)
    return [{"source": SOURCE, "id": d["id"], "title": d["title"], "summary": d["description"][:300],
             "url": d["url"], "updated": d["updated"]} for _, d in scored[:limit]]


def describe(dataset_id: str) -> dict:
    for d in _catalogue():
        if d["id"] == dataset_id:
            return {"source": SOURCE, "id": d["id"], "title": d["title"], "description": d["description"][:1000],
                    "publisher": d["publisher"], "updated": d["updated"], "url": d["url"],
                    "resources": d["resources"]}
    raise LookupError(f"No London Datastore dataset with id {dataset_id!r}")
