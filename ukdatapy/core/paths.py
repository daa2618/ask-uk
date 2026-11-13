"""Cache folder, the daily JSON cache and the JSON-lines writer, from onspy."""
import datetime
import hashlib
import json
import os
from pathlib import Path

from ukdatapy.core import audit


def cache_dir() -> Path:
    """Folder for cached files. Not created here.

    The UKDATAPY_CACHE_DIR environment variable, else ~/.cache/ukdatapy. Never a .env file: the server starts inside
    the project the agent opened, so a cloned repo's .env could point the cache at planted files, which are served
    with no fetch and so skip the allowlist and TLS.
    """
    value = os.environ.get("UKDATAPY_CACHE_DIR")
    return Path(value).expanduser() if value else Path.home() / ".cache" / "ukdatapy"


def load_daily_json(prefix: str, fetch):
    """Load today's `<cache_dir>/<prefix>_<YYYYMMDD>.json`, or call `fetch()` and write it. None is not cached."""
    today = cache_dir() / f"{prefix}_{datetime.date.today():%Y%m%d}.json"
    if today.exists():
        return json.loads(today.read_text())
    data = fetch()
    if data is not None:
        today.parent.mkdir(parents=True, exist_ok=True)
        today.write_text(json.dumps(data))
    return data


def load_daily_bytes(key: str, fetch) -> bytes:
    """Today's cached copy of a downloaded file, keyed by `key` (a URL), or `fetch()` written to the cache."""
    name = hashlib.sha256(key.encode()).hexdigest()[:32]
    today = cache_dir() / "files" / f"{name}_{datetime.date.today():%Y%m%d}"
    if today.exists():
        data = today.read_bytes()
        audit.record_fetch(key, "cache", size=len(data))  # today's earlier "ok" fetch of this URL downloaded it
        return data
    data = fetch()
    today.parent.mkdir(parents=True, exist_ok=True)
    today.write_bytes(data)
    return data


def append_jsonl(path: Path, record: dict) -> None:
    """Append `record` to `path` as one JSON line, creating the folder. From onspy (its plan 003, R3).

    The line goes out in a single write() on a file opened O_APPEND, so lines from servers running at the same
    time do not mix on a local disk. Raises OSError when the file cannot be written.
    """
    data = (json.dumps(record) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise OSError(f"short write to {path}")
    finally:
        os.close(fd)
