"""The allowlist is the security boundary: every request the server makes goes through ukdatapy.core.http."""
import _socket
import socket

import pytest

from ukdatapy.core import audit, http


@pytest.mark.parametrize("url", [
    "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23",
    "https://api.beta.ons.gov.uk/v1/search?q=cpi",
    "https://data.london.gov.uk/api/v2/datasets/export.json",
    "https://data.gov.uk/api/3/action/package_show?id=x",
    "https://assets.publishing.service.gov.uk/media/abc/file.csv",
    "https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/region_traffic_by_vehicle_type.csv",
    "https://s3.eu-west-1.amazonaws.com/data.defra.gov.uk/statistics_2024/file.csv",
    "https://s3.eu-west-1.amazonaws.com/crowding.data.tfl.gov.uk/?list-type=2",
    "https://s3.eu-west-1.amazonaws.com/cycling.data.tfl.gov.uk/?list-type=2",
    "https://crowding.data.tfl.gov.uk/Network%20Demand/StationFootfall_2025_2026%20.csv",
])
def test_allowed(url):
    assert http.is_allowed(url)


@pytest.mark.parametrize("url", [
    "http://www.ons.gov.uk/plain-http",
    "https://evil.example.com/",
    "https://gov.uk.evil.example.com/",
    "https://notgov.uk/",
    "https://storage.googleapis.com/another-bucket/file.csv",
    "https://s3.eu-west-1.amazonaws.com/crowding.data.tfl.gov.uk.evil/?list-type=2",
    "https://s3.eu-west-1.amazonaws.com/another-bucket/?list-type=2",
    "https://storage.googleapis.com/dft-statistics/../another-bucket/file.csv",
    "https://storage.googleapis.com/dft-statistics/%2e%2e/another-bucket/file.csv",
    "https://www.ons.gov.uk@evil.example.com/",
    "ftp://www.ons.gov.uk/",
    "https://evil.example.com\\.gov.uk/x",
    "https://169.254.169.254\\.gov.uk/latest/meta-data/",
    "https://www.ons.gov.uk\\@evil.example.com/",
    "https://ons.gov.uk:8443/x",
    "https://www.ons.gov.uk/a b",
    "https://www.ons.gov.uk/a\tb",
])
def test_blocked(url):
    assert not http.is_allowed(url)


def test_get_refuses_a_blocked_url_without_a_request(fake_http):
    with pytest.raises(http.BlockedURL):
        http.get("https://evil.example.com/data.csv")
    assert fake_http.calls == []


def test_redirect_to_a_blocked_host_is_refused(fake_http):
    fake_http.add("https://www.ons.gov.uk/file", status=302, headers={"Location": "https://evil.example.com/x"})
    with pytest.raises(http.BlockedURL):
        http.get("https://www.ons.gov.uk/file")
    assert [c["url"] for c in fake_http.calls] == ["https://www.ons.gov.uk/file"]


def test_backslash_url_makes_no_request(fake_http):
    with pytest.raises(http.BlockedURL):
        http.get("https://evil.example.com\\.gov.uk/x")
    assert fake_http.calls == []


def test_redirect_to_a_backslash_host_is_refused(fake_http):
    fake_http.add("https://www.ons.gov.uk/r", status=302, headers={"Location": "https://evil.example.com\\.gov.uk/x"})
    with pytest.raises(http.BlockedURL):
        http.get("https://www.ons.gov.uk/r")


def test_port_443_is_allowed():
    assert http.is_allowed("https://www.ons.gov.uk:443/x")


def test_get_caps_the_body_too(fake_http):
    fake_http.add("https://www.ons.gov.uk/big.json", content=b"x" * 101)
    with pytest.raises(http.HttpError, match="larger than"):
        http.get("https://www.ons.gov.uk/big.json", max_bytes=100)


def test_redirect_within_the_allowlist_is_followed(fake_http):
    fake_http.add("https://data.gov.uk/a", status=301, headers={"Location": "https://www.data.gov.uk/a"})
    fake_http.add("https://www.data.gov.uk/a", text="ok")
    assert http.get("https://data.gov.uk/a").text == "ok"


def test_relative_redirect_is_resolved_and_checked(fake_http):
    fake_http.add("https://www.ons.gov.uk/a", status=302, headers={"Location": "/b"})
    fake_http.add("https://www.ons.gov.uk/b", text="ok")
    assert http.get("https://www.ons.gov.uk/a").text == "ok"


def test_too_many_redirects(fake_http):
    fake_http.add("https://www.ons.gov.uk/loop", status=302, headers={"Location": "https://www.ons.gov.uk/loop"})
    with pytest.raises(http.HttpError, match="redirects"):
        http.get("https://www.ons.gov.uk/loop")


def test_requests_never_follow_redirects_themselves(fake_http):
    fake_http.add("https://www.ons.gov.uk/x", text="ok")
    http.get("https://www.ons.gov.uk/x")
    assert fake_http.calls[0]["allow_redirects"] is False


def test_download_returns_bytes(fake_http):
    fake_http.add("https://www.ons.gov.uk/f.csv", content=b"a,b\n1,2\n")
    assert http.download("https://www.ons.gov.uk/f.csv") == b"a,b\n1,2\n"


def test_download_over_the_size_cap_is_refused(fake_http):
    fake_http.add("https://www.ons.gov.uk/big.csv", content=b"x" * 101)
    with pytest.raises(http.HttpError, match="larger than"):
        http.download("https://www.ons.gov.uk/big.csv", max_bytes=100)


def test_http_error_status_raises(fake_http):
    fake_http.add("https://www.ons.gov.uk/missing", status=404, text="nope")
    with pytest.raises(http.HttpError, match="404") as err:
        http.get("https://www.ons.gov.uk/missing")
    assert err.value.status == 404


def test_allowlist_file_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "allow.toml"
    path.write_text('host_suffixes = ["example.org"]\nurl_prefixes = []\n')
    monkeypatch.setenv("UKDATAPY_ALLOWLIST", str(path))
    assert http.is_allowed("https://data.example.org/x")
    assert not http.is_allowed("https://www.ons.gov.uk/")


# A name on the allowlist can still resolve to a private address. The check runs on the connected socket, so a
# DNS answer that changes between a pre-check and the connection (DNS rebinding) is caught too.
@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
    # IPv6 forms that carry an IPv4 address: a NAT64 or 6to4 gateway connects to the IPv4 address inside.
    "64:ff9b::a00:5", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",  # NAT64 well-known prefix: 10.0.0.5, 127.0.0.1, metadata
    "2002:a00:5::1", "2002:7f00:1::1",                           # 6to4: 10.0.0.5, 127.0.0.1
    "64:ff9b:1::a00:5",                                          # local-use NAT64: where the IPv4 sits varies
    "::a00:5",                                                   # IPv4-compatible (obsolete)
])
def test_private_and_local_addresses_are_not_public(address):
    assert not http.is_public_address(address)


# 64:ff9b::9765:90 is 151.101.0.144 (GOV.UK) through NAT64: how an IPv6-only network reaches GOV.UK, so it must pass.
@pytest.mark.parametrize("address", ["151.101.0.144", "2a04:4e42::144", "::ffff:151.101.0.144",
                                     "64:ff9b::9765:90", "2002:9765:90::1"])
def test_internet_addresses_are_public(address):
    assert http.is_public_address(address)


@pytest.fixture
def local_listener(monkeypatch):
    """A real TCP listener on 127.0.0.1. Connections complete in the backlog; nothing is ever sent back.
    conftest blocks socket.connect in every test; this lets connections to 127.0.0.1 through, and only those."""
    def connect(sock, address):
        if address[0] != "127.0.0.1":
            raise RuntimeError("Network access in a test")
        return _socket.socket.connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    server = socket.create_server(("127.0.0.1", 0))
    yield server.getsockname()[1]
    server.close()


def test_a_connection_to_a_local_address_is_closed_before_tls(local_listener):
    conn = http._PublicOnlyConnection("127.0.0.1", port=local_listener, timeout=5)
    with pytest.raises(http.BlockedURL, match="127.0.0.1"):
        conn.connect()


def test_an_allowlisted_name_resolving_to_a_local_address_is_blocked_and_logged(local_listener, monkeypatch):
    real = socket.getaddrinfo

    def dns(host, port, *args, **kwargs):  # the attacker's DNS answer for a name inside gov.uk
        if host == "rebind.example.gov.uk":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", local_listener))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    fetches = []
    token = audit._fetches.set(fetches)
    try:
        with pytest.raises(http.BlockedURL, match="not a public address"):
            http.get("https://rebind.example.gov.uk/data.csv", timeout=5)
    finally:
        audit._fetches.reset(token)
    assert [(f["url"], f["outcome"]) for f in fetches] == [("https://rebind.example.gov.uk/data.csv", "blocked")]
