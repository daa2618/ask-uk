"""The audit log: one JSON line per tool call in cache_dir()/calls_<YYYY-MM>.jsonl, with every URL it fetched."""
import datetime
import json
import multiprocessing
import subprocess
from pathlib import Path

import pytest
import requests
from test_server import CSV, call, fake_source, sources  # noqa: F401 -- sources is a fixture, used by every test below

import ukdatapy
from ukdatapy import server
from ukdatapy.core import audit, paths

pytestmark = pytest.mark.usefixtures("sources")


def records():
    month = f"{datetime.date.today():%Y-%m}"
    log = paths.cache_dir() / f"calls_{month}.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_one_line_per_tool_call_with_tool_arguments_and_client():
    call("search_datasets", {"query": "rents", "sources": ["london"]})
    call("describe_dataset", {"source": "london", "dataset_id": "x1"})
    first, second = records()
    assert first["tool"] == "search_datasets"
    assert first["arguments"] == {"query": "rents", "sources": ["london"]}
    assert first["outcome"] == "ok" and first["error"] is None
    assert second["tool"] == "describe_dataset"
    assert first["call_id"] != second["call_id"]
    assert first["client"]["name"]  # whichever MCP client connected, not only Claude
    assert first["ukdatapy_version"] and first["python"] and first["pid"]
    assert isinstance(first["duration_ms"], int)
    datetime.datetime.fromisoformat(first["started"])


def test_each_site_fetched_is_recorded(fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    [fetch] = records()[0]["fetches"]
    assert fetch["url"] == "https://data.london.gov.uk/f.csv"
    assert fetch["outcome"] == "ok" and fetch["status"] == 200 and fetch["bytes"] == len(CSV)
    assert isinstance(fetch["ms"], int)


def test_a_file_read_from_the_cache_is_recorded_as_a_cache_fetch(fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    downloaded, cached = (r["fetches"] for r in records())
    assert [f["outcome"] for f in downloaded] == ["ok"]
    [fetch] = cached
    assert fetch["url"] == "https://data.london.gov.uk/f.csv"
    assert fetch["outcome"] == "cache" and fetch["status"] is None and fetch["bytes"] == len(CSV)
    assert len(fake_http.calls) == 1  # the second call really did read the cache, not the network


def test_each_redirect_hop_is_recorded(fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", status=302,
                  headers={"Location": "https://data.london.gov.uk/g.csv"})
    fake_http.add("https://data.london.gov.uk/g.csv", content=CSV)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    hops = records()[0]["fetches"]
    assert [(h["url"], h["outcome"], h["status"]) for h in hops] == [
        ("https://data.london.gov.uk/f.csv", "redirect", 302), ("https://data.london.gov.uk/g.csv", "ok", 200)]


def test_a_url_off_the_allowlist_is_recorded_as_blocked(fake_http):
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "evil"})
    [record] = records()
    assert record["outcome"] == "error" and record["error"]["type"] == "ToolError"
    assert record["fetches"] == [{"url": "https://evil.example.com/f.csv", "outcome": "blocked",
                                  "status": None, "bytes": None, "ms": None, "error": None}]


def test_an_http_error_status_is_recorded(fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", status=404)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    [fetch] = records()[0]["fetches"]
    assert (fetch["outcome"], fetch["status"]) == ("http_error", 404)


def test_a_connection_failure_is_recorded(monkeypatch):
    def refuse(self, url, **kwargs):
        raise requests.ConnectionError("connection refused")
    monkeypatch.setattr(requests.Session, "get", refuse)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1"})
    record = records()[0]
    assert record["outcome"] == "error"
    [fetch] = record["fetches"]
    assert fetch["outcome"] == "failed" and "connection refused" in fetch["error"]


def test_a_tool_error_is_recorded_with_its_message():
    result = call("describe_dataset", {"source": "london", "dataset_id": "nope"})
    assert result.is_error
    [record] = records()
    assert record["outcome"] == "error"
    assert record["error"]["type"] == "ToolError" and "No dataset 'nope'" in record["error"]["message"]


def test_a_log_that_cannot_be_written_warns_on_stderr_and_the_tool_still_answers(monkeypatch, tmp_path,
                                                                                   capfd):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("")
    monkeypatch.setenv("UKDATAPY_CACHE_DIR", str(blocker))
    result = call("describe_dataset", {"source": "london", "dataset_id": "x1"})
    assert not result.is_error and result.data["id"] == "x1"
    out, err = capfd.readouterr()
    assert "ukdatapy: call not logged:" in err
    assert out == ""  # stdout carries the MCP protocol under stdio


def test_fetches_outside_a_tool_call_are_not_recorded(fake_http):
    from ukdatapy.core import http
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    http.get("https://data.london.gov.uk/f.csv")
    assert not list(paths.cache_dir().glob("calls_*.jsonl"))


def _append_many(path, n):
    for i in range(n):
        paths.append_jsonl(path, {"i": i, "pad": "x" * 20_000})


def test_lines_from_parallel_processes_do_not_mix(tmp_path):
    log = tmp_path / "calls.jsonl"
    procs = [multiprocessing.get_context("spawn").Process(target=_append_many, args=(log, 50)) for _ in range(20)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    lines = log.read_text().splitlines()
    assert len(lines) == 1000 and all(json.loads(line)["pad"] for line in lines)


def test_the_server_registers_the_audit_middleware():
    from ukdatapy.core import audit
    assert any(isinstance(m, audit.AuditMiddleware) for m in server.mcp.middleware)


def test_a_search_records_how_many_results_came_back():
    server.SOURCES["govuk"] = fake_source("govuk")  # the fixture's fakes; no hits: the gap the log should show
    call("search_datasets", {"query": "rents", "sources": ["london"]})
    call("search_datasets", {"query": "k-pop concerts", "sources": ["govuk"]})
    found, empty = records()
    assert found["results"] == 1
    assert empty["results"] == 0


def test_preview_data_records_how_many_rows_matched(fake_http):
    fake_http.add("https://data.london.gov.uk/f.csv", content=CSV)
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1", "limit": 1})
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1", "where": {"name": "Z"}})
    call("preview_data", {"source": "london", "dataset_id": "x1", "resource_id": "r1", "group_by": ["year"]})
    assert [r["results"] for r in records()] == [3, 0, 2]  # every match, not the page; groups for group_by


def test_tools_that_are_not_searches_record_no_result_count():
    call("describe_dataset", {"source": "london", "dataset_id": "x1"})
    assert records()[0]["results"] is None


def test_each_line_records_where_the_answering_server_is_installed():
    call("search_datasets", {"query": "rents", "sources": ["london"]})
    assert records()[0]["server_path"] == str(Path(ukdatapy.__file__).resolve().parent)


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def _package_in_repo(tmp_path):
    package = tmp_path / "repo" / "ukdatapy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    _git(package.parent, "init", "-q")
    _git(package.parent, "add", "-A")
    _git(package.parent, "commit", "-q", "-m", "c")
    return package


def test_a_package_tracked_in_git_records_its_commit_and_whether_it_has_uncommitted_changes(tmp_path):
    package = _package_in_repo(tmp_path)
    assert audit._git_state(package) == (_git(package, "rev-parse", "HEAD"), False)
    (package / "__init__.py").write_text("x = 1\n")
    assert audit._git_state(package) == (_git(package, "rev-parse", "HEAD"), True)


def test_a_change_outside_the_package_does_not_count_as_uncommitted(tmp_path):
    package = _package_in_repo(tmp_path)
    (package.parent / "notes.md").write_text("draft\n")
    assert audit._git_state(package)[1] is False


def test_a_pip_installed_package_inside_a_clone_records_no_commit(tmp_path):
    clone = _package_in_repo(tmp_path).parent  # like an ask-uk clone, its .venv not tracked
    installed = clone / ".venv" / "lib" / "ukdatapy"
    installed.mkdir(parents=True)
    (installed / "__init__.py").write_text("")
    assert audit._git_state(installed) == (None, None)


def test_a_package_outside_any_git_repo_records_no_commit(tmp_path):
    (tmp_path / "__init__.py").write_text("")
    assert audit._git_state(tmp_path) == (None, None)


def test_each_line_records_the_servers_git_state():
    call("search_datasets", {"query": "rents", "sources": ["london"]})
    row = records()[0]
    assert (row["git_sha"], row["git_dirty"]) == (audit.GIT_SHA, audit.GIT_DIRTY)


def test_a_dotenv_in_the_working_directory_does_not_move_the_cache(tmp_path, monkeypatch):
    # The agent starts the server inside the project it opened. A cloned repo's .env could otherwise point the cache
    # at its own planted files, served as official data with no fetch, and pull the audit log into the repo.
    (tmp_path / ".env").write_text("UKDATAPY_CACHE_DIR=./planted\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UKDATAPY_CACHE_DIR")
    assert paths.cache_dir() == tmp_path / "home" / ".cache" / "ukdatapy"
