"""Data files linked from a publisher's web page."""
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ukdatapy.core import http

EXTENSIONS = (".csv", ".xlsx", ".xls", ".ods")


def file_links(html: bytes, page_url: str) -> list[dict]:
    """CSV, XLSX, XLS and ODS links on the page, in page order, de-duplicated by URL.

    Only https links on the allowlist count. GOV.UK's /csv-preview/ pages repeat its CSV links and are skipped.
    GOV.UK also puts an image-only link before the titled one, so a later link can fill an empty name.
    """
    links = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        path = urlsplit(url).path
        if (not path.lower().endswith(EXTENSIONS) or "/csv-preview/" in path
                or not url.startswith("https://") or not http.is_allowed(url)):
            continue
        name = " ".join((a.get("aria-label") or a.text).split())
        if url in links:
            links[url]["name"] = links[url]["name"] or name
        else:
            links[url] = {"name": name, "url": url, "format": path.rsplit(".", 1)[1].upper()}
    return list(links.values())
