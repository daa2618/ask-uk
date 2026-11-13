"""Transport for London open data: the files in TfL's two public S3 buckets.

TfL publishes its statistics (station entries and exits, network demand, bus demand, cycle hire journeys, cycle
counts) as files in crowding.data.tfl.gov.uk and cycling.data.tfl.gov.uk. The bucket hosts serve a JavaScript
browser, so the file list comes from S3 ListObjectsV2 on s3.eu-west-1.amazonaws.com, fetched once a day.
Each folder that holds files is one dataset, with id `<bucket short name>/<folder path>`.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from urllib.parse import quote

from ukdatapy.core import http
from ukdatapy.core.paths import load_daily_json

SOURCE = "tfl"
BUCKETS = {"crowding": "crowding.data.tfl.gov.uk", "cycling": "cycling.data.tfl.gov.uk"}
LIST_URL = "https://s3.eu-west-1.amazonaws.com/{}/"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
WEIGHTS = (("description", 3), ("title", 2), ("files", 1))  # the hand-written line says most
# Every search_datasets call reaches tfl, so words any question might share with a folder line do not count.
STOPWORDS = {"a", "an", "and", "are", "as", "at", "by", "data", "dataset", "for", "from", "how", "in", "is",
             "london", "many", "much", "of", "on", "or", "per", "the", "tfl", "to", "uk", "what", "which", "with"}

# File names say little (NUMBAT, BUSTO), so each top folder gets one line. Checked on 2026-11-12 against one file
# per folder; CycleCounters, CycleParking, CycleRoutes, CyclingInfrastructure and RODS are from file names only.
DESCRIPTIONS = {
    "crowding/Annual Station Counts": "Annualised entries and exits at each Underground and TfL rail station, "
                                      "by day of the week; one file per year.",
    "crowding/BUSTO": "Bus demand: boardings, alightings and load at each stop, by route, quarter hour and timeband, "
                      "per financial year, with a user guide and data dictionary.",
    "crowding/Crowding": "Underground train loads between stations by quarter hour, and a list of station nodes.",
    "crowding/NUMBAT": "NUMBAT rail demand model outputs: station boardings and alightings, line loads and "
                       "origin-destination flows, by day type and year.",
    "crowding/Network Demand": "Daily entry and exit tap counts at each station (station footfall), "
                               "and daily Tube and bus journey counts.",
    "crowding/RODS": "Rolling Origin and Destination Survey of Underground journeys (zip).",
    "cycling/ActiveTravelCountsProgramme": "Street counts by mode in 15-minute intervals at survey sites in central, "
                                           "inner and outer London and on Cycleways, since 2014: cycles, and in 2025 "
                                           "files also pedestrians, cargo bikes and e-scooters (pedestrian footfall).",
    "cycling/CycleCounters": "Readings from automatic cycle counters at Blackfriars and Embankment.",
    "cycling/CycleCountsDashboardDocumentation": "Cycle counts per site and year since 2015, spring and autumn, "
                                                 "with borough and location, and a user guide.",
    "cycling/CycleParking": "Cycle parking locations (zip).",
    "cycling/CycleRoutes": "Cycle route lines (JSON and KML).",
    "cycling/CyclingInfrastructure": "Cycling Infrastructure Database: cycle lanes, parking, signals and other "
                                     "assets (JSON), with documentation.",
    "cycling/usage-stats": "Santander Cycles (cycle hire) journeys: one row per hire, in extracts since 2012.",
}


def _words(text: str) -> str:
    """Split camelCase and separators so "StationFootfall_2025" reads as "Station Footfall 2025"."""
    return re.sub(r"[_\-./]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text))


def _list(bucket: str) -> list[dict]:
    files, params = [], {"list-type": "2"}
    while True:
        root = ET.fromstring(http.get(LIST_URL.format(bucket), params=params).content)
        for c in root.findall("s:Contents", NS):
            key = c.findtext("s:Key", "", NS)
            if "/" in key and not key.endswith("/"):  # skip bucket-root pages and folder markers
                files.append({"key": key, "updated": c.findtext("s:LastModified", "", NS)[:10],
                              "bytes": int(c.findtext("s:Size", "0", NS))})
        token = root.findtext("s:NextContinuationToken", None, NS)
        if root.findtext("s:IsTruncated", "false", NS) != "true" or not token:
            return files
        params = {"list-type": "2", "continuation-token": token}


def _build() -> list[dict]:
    datasets = {}
    for short, bucket in BUCKETS.items():
        for f in _list(bucket):
            folder, _, name = f["key"].rpartition("/")
            d = datasets.setdefault(f"{short}/{folder}", {"id": f"{short}/{folder}", "bucket": bucket,
                                                          "folder": folder, "files": []})
            d["files"].append({"id": name, "name": name, "format": PurePosixPath(name).suffix.lstrip(".").upper(),
                               "url": f"https://{bucket}/{quote(f['key'])}", "updated": f["updated"],
                               "bytes": f["bytes"]})
    out = []
    for d in datasets.values():
        d["files"].sort(key=lambda r: r["updated"], reverse=True)
        top = f"{d['id'].split('/')[0]}/{d['folder'].split('/')[0]}"
        out.append({"id": d["id"], "title": f"TfL {d['folder'].replace('/', ' ')}",
                    "description": DESCRIPTIONS.get(top, ""), "updated": d["files"][0]["updated"],
                    "url": f"https://{d['bucket']}/#{d['folder']}/", "resources": d["files"]})
    return out


def _catalogue() -> list[dict]:
    return load_daily_json("tfl_catalogue", _build)


def _score(dataset: dict, words: list[str]) -> int:
    """Weighted matches, or 0 unless at least half the query words match somewhere."""
    fields = {"title": _words(dataset["title"]), "description": dataset["description"],
              "files": _words(" ".join(r["name"] for r in dataset["resources"]))}
    hits = {(word, name): weight for word in words for name, weight in WEIGHTS
            if re.search(r"\b" + re.escape(word), fields[name], re.IGNORECASE)}
    matched = {word for word, _ in hits}
    return sum(hits.values()) if 2 * len(matched) >= len(words) else 0


def search(query: str, limit: int = 10) -> list[dict]:
    words = [w for w in dict.fromkeys(re.findall(r"\w+", query.lower())) if w not in STOPWORDS]
    if not words:
        return []
    scored = [(s, d) for d in _catalogue() if (s := _score(d, words)) > 0]
    scored.sort(key=lambda sd: (sd[0], sd[1]["updated"]), reverse=True)
    hits = []
    for _, d in scored[:limit]:
        files = ", ".join(r["name"] for r in d["resources"][:5])
        hits.append({"source": SOURCE, "id": d["id"], "title": d["title"],
                     "summary": f"{d['description']} Files: {files}".strip()[:300], "url": d["url"],
                     "updated": d["updated"]})
    return hits


def describe(dataset_id: str) -> dict:
    for d in _catalogue():
        if d["id"] == dataset_id:
            return {"source": SOURCE, "id": d["id"], "title": d["title"], "description": d["description"],
                    "publisher": "Transport for London", "updated": d["updated"], "url": d["url"],
                    "resources": d["resources"]}
    raise LookupError(f"No TfL folder {dataset_id!r}; ids look like 'crowding/Network Demand'")
