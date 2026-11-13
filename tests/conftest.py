"""Offline test harness, copied from onspy: every HTTP call goes through a fake requests.Session.get.

Any URL without a route fails the test, and raw sockets are blocked as a second guard.
"""
import json as jsonlib
import socket
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import requests

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeHttp:
    def __init__(self):
        self.routes = {}
        self.calls = []

    @staticmethod
    def _key(url, params):
        # http.get puts params into the URL itself, so compare base URL plus the decoded, sorted query.
        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True) + list((params or {}).items())
        base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return f"{base}?{urlencode(sorted(query))}" if query else base

    def add(self, url, *, json=None, text=None, content=None, status=200, headers=None, params=None):
        if json is not None:
            content = jsonlib.dumps(json).encode()
        elif text is not None:
            content = text.encode()
        resp = requests.Response()
        resp.status_code = status
        resp._content = content or b""
        resp._content_consumed = True  # lets iter_content() yield _content
        resp.headers.update(headers or {})
        resp.url = url
        resp.encoding = "utf-8"
        self.routes[self._key(url, params)] = resp

    def get(self, session, url, params=None, **kwargs):
        self.calls.append({"url": url, "params": params, **kwargs})
        key = self._key(url, params)
        if key not in self.routes:
            raise AssertionError(f"No fake route for {key}")
        return self.routes[key]


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Network access in a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def fake_http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: fake.get(self, url, **kw))
    return fake


@pytest.fixture(autouse=True)
def _cache_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("UKDATAPY_CACHE_DIR", str(tmp_path / "ukdatapy_cache"))
    monkeypatch.delenv("UKDATAPY_ALLOWLIST", raising=False)


def fixture_bytes(name):
    return (FIXTURES / name).read_bytes()


def fixture_json(name):
    return jsonlib.loads((FIXTURES / name).read_text())
