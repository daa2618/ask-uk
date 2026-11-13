"""Every HTTP request ukdatapy makes goes through get(), which enforces the allowlist and a size cap.

Redirects are followed here, one hop at a time, so each hop is checked too.
The retry policy comes from onspy: retry 429 and 503 three times and cap Retry-After at MAX_WAIT seconds.
"""
import ipaddress
import os
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util import parse_url
from urllib3.util.retry import Retry

from ukdatapy.core import audit

USER_AGENT = "ukdatapy/0.1"
MAX_WAIT = 10
MAX_REDIRECTS = 5
MAX_BYTES = 100 * 1024 * 1024
DEFAULT_ALLOWLIST = Path(__file__).resolve().parent.parent / "allowlist.toml"


class BlockedURL(ValueError):
    """The URL is not on the allowlist."""


class HttpError(RuntimeError):
    """The request failed: a bad status, too many redirects or a file over the size cap."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _CappedRetry(Retry):
    """urllib3 sleeps for any Retry-After the server sends; cap it so a long ban fails fast."""

    def get_retry_after(self, response):
        seconds = super().get_retry_after(response)
        return None if seconds is None else min(seconds, MAX_WAIT)

    def sleep(self, response=None):
        # stderr, never stdout: under the stdio transport stdout carries the MCP protocol.
        last = self.history[-1]
        print(f"{urlsplit(last.url or '').netloc} answered {last.status or last.error}; retrying", file=sys.stderr)
        super().sleep(response)


RETRY = _CappedRetry(total=3, status_forcelist=(429, 503), backoff_factor=1, backoff_max=MAX_WAIT,
                     raise_on_status=False, redirect=0)


def _allowlist():
    path = Path(os.environ.get("UKDATAPY_ALLOWLIST") or DEFAULT_ALLOWLIST)
    data = tomllib.loads(path.read_text())
    return data.get("host_suffixes", []), data.get("url_prefixes", [])


def is_allowed(url: str) -> bool:
    # urlsplit and urllib3 disagree on some URLs: "https://evil.com\\.gov.uk/" is a .gov.uk host to urlsplit,
    # but urllib3 connects to evil.com. Refuse backslashes, whitespace and control characters outright, and
    # require both parsers to see the same host.
    if "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    if parts.port not in (None, 443) or (parse_url(url).host or "").lower() != parts.hostname.lower():
        return False
    if ".." in unquote(parts.path).split("/"):
        return False
    host = parts.hostname.lower()
    suffixes, prefixes = _allowlist()
    if any(host == s or host.endswith("." + s) for s in suffixes):
        return True
    return any(url.startswith(p) for p in prefixes)


NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
LOCAL_NAT64_PREFIX = ipaddress.ip_network("64:ff9b:1::/48")


def is_public_address(address: str) -> bool:
    """True for an internet address; False for loopback, private, link-local (cloud metadata) and other
    reserved ranges, including IPv4 addresses carried inside IPv6.

    An IPv6 address can carry an IPv4 one that a gateway then connects to: IPv4-mapped (::ffff:a.b.c.d), NAT64
    (64:ff9b::a.b.c.d) and 6to4 (2002:aabb:ccdd::). The stdlib calls the NAT64 and 6to4 ranges global, so the IPv4
    address inside is checked instead; a NAT64 address for a public site, which is how an IPv6-only network reaches
    GOV.UK, still passes. Local-use NAT64 (64:ff9b:1::/48) puts the IPv4 address where its operator chooses, and
    IPv4-compatible (::a.b.c.d) is obsolete, so both are refused. A network's own NAT64 prefix cannot be recognised.
    """
    ip = ipaddress.ip_address(address)
    if ip.version == 6:
        if ip in LOCAL_NAT64_PREFIX or int(ip) >> 32 == 0:
            return False
        if ip in NAT64_PREFIX:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        else:
            ip = ip.ipv4_mapped or ip.sixtofour or ip
    return ip.is_global


class _PublicOnlyConnection(HTTPSConnection):
    """Refuses a connection whose socket reached a non-public address, before any TLS or HTTP is sent.

    The allowlist checks host names; this checks where the name actually led. Checking the connected socket,
    not a DNS lookup made beforehand, also catches a DNS answer that changes between the two (DNS rebinding).
    Proxies are off (trust_env = False), so the peer is always the real server.
    """

    def _new_conn(self):
        sock = super()._new_conn()
        address = sock.getpeername()[0]
        if not is_public_address(address):
            sock.close()
            raise BlockedURL(f"{self.host} resolved to {address}, which is not a public address")
        return sock


class _PublicOnlyPool(HTTPSConnectionPool):
    ConnectionCls = _PublicOnlyConnection


class _PublicOnlyAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": HTTPConnectionPool, "https": _PublicOnlyPool}


def _session():
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", _PublicOnlyAdapter(max_retries=RETRY))
    return session


def get(url: str, *, params=None, headers=None, timeout: float = 30.0, max_bytes: int = MAX_BYTES
        ) -> requests.Response:
    """GET an allowlisted URL and read its body, at most max_bytes.

    Raises BlockedURL before any request to a URL off the list, or a redirect off it, and HttpError on a status
    of 400 or more, too many redirects or a body over max_bytes.
    """
    session = _session()
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    # Check each URL exactly as requests will send it, query string included; redirects are prepared below.
    url = requests.Request("GET", url, params=params).prepare().url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_allowed(url):
            audit.record_fetch(url, "blocked")
            raise BlockedURL(f"Not on the ukdatapy allowlist: {url}")
        started = time.monotonic()
        try:
            resp = session.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                audit.record_fetch(url, "redirect", status=resp.status_code, started=started)
                url = requests.Request("GET", urljoin(url, resp.headers["Location"])).prepare().url
                continue
            if resp.status_code >= 400:
                audit.record_fetch(url, "http_error", status=resp.status_code, started=started)
                raise HttpError(f"HTTP {resp.status_code} from {url}", status=resp.status_code)
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                audit.record_fetch(url, "too_large", status=resp.status_code, started=started)
                raise HttpError(f"{url} is larger than {max_bytes} bytes")
            chunks, total = [], 0
            for chunk in resp.iter_content(1 << 16):
                total += len(chunk)
                if total > max_bytes:
                    audit.record_fetch(url, "too_large", status=resp.status_code, started=started)
                    raise HttpError(f"{url} is larger than {max_bytes} bytes")
                chunks.append(chunk)
        except BlockedURL:
            audit.record_fetch(url, "blocked", started=started)
            raise
        except requests.RequestException as e:
            audit.record_fetch(url, "failed", started=started, error=e)
            raise
        resp._content = b"".join(chunks)
        audit.record_fetch(url, "ok", status=resp.status_code, size=total, started=started)
        return resp
    raise HttpError(f"More than {MAX_REDIRECTS} redirects from {url}")


def download(url: str, *, max_bytes: int = MAX_BYTES) -> bytes:
    """The body of an allowlisted URL, at most max_bytes."""
    return get(url, timeout=60.0, max_bytes=max_bytes).content
