"""Audit log: one JSON line per tool call in cache_dir()/calls_<YYYY-MM>.jsonl, with every URL the call fetched.

The user's prompt is not recorded; MCP does not send it to servers. The client's name and version come from the
MCP connection, so calls from any agent are recorded the same way. A log that cannot be written costs one stderr
warning, never the tool call. stdout is never written: under the stdio transport it carries the MCP protocol.
"""
import os
import platform
import subprocess
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastmcp.server.middleware import Middleware

from ukdatapy.core import paths

_fetches: ContextVar[list | None] = ContextVar("ukdatapy_fetches", default=None)
# The installed package folder: tells a clone's server (such as ask-uk's .venv) from a dev checkout's.
SERVER_PATH = str(Path(__file__).resolve().parent.parent)


def _git_state(package):
    """(commit, uncommitted changes in the package folder) when the package is tracked in git, as in an editable
    dev install. (None, None) for a pip install: its copy has no history, even inside a clone's .venv."""
    def git(*args):
        return subprocess.run(["git", "-C", str(package), *args], capture_output=True, text=True, timeout=5,
                              check=True).stdout
    try:
        git("ls-files", "--error-unmatch", "__init__.py")
        return git("rev-parse", "HEAD").strip(), bool(git("status", "--porcelain", "--", ".").strip())
    except (OSError, subprocess.SubprocessError):  # no git, no repo, or the package is not tracked
        return None, None


# Read once when the server starts: the code that answers every call in this process.
GIT_SHA, GIT_DIRTY = _git_state(SERVER_PATH)


def record_fetch(url, outcome, *, status=None, size=None, started=None, error=None):
    """Note one HTTP request (or a refused URL) against the tool call in progress. No-op outside a tool call."""
    fetches = _fetches.get()
    if fetches is not None:
        fetches.append({"url": url, "outcome": outcome, "status": status, "bytes": size,
                        "ms": None if started is None else round((time.monotonic() - started) * 1000),
                        "error": None if error is None else str(error)[:500]})


def _version():
    try:
        return version("ukdatapy")
    except PackageNotFoundError:
        return None


def _result_count(result):
    """How many hits a search returned, or how many rows preview_data matched (groups, with group_by), so an
    answer of nothing shows in the log. None for other tools.
    A non-zero search count can still be all off-topic: GOV.UK search always returns its closest matches."""
    content = getattr(result, "structured_content", None)
    if isinstance(content, dict) and isinstance(content.get("results"), list):
        return len(content["results"])
    if isinstance(content, dict) and isinstance(content.get("total_rows"), int):
        return content["total_rows"]
    return None


def _client(context):
    """{name, version} of the MCP client: per-request _meta on protocol 2026-07-28, the initialize call before."""
    ctx = context.fastmcp_context
    try:
        meta = ctx.request_context.meta or {}
        info = meta.get("io.modelcontextprotocol/clientInfo") if isinstance(meta, dict) else None
        if info:
            return {"name": info.get("name"), "version": info.get("version")}
        params = ctx.session.client_params
        if params is not None:
            return {"name": params.client_info.name, "version": params.client_info.version}
    except (AttributeError, RuntimeError):  # client-supplied data; a missing field must not fail the call
        pass
    return None


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        started, clock = datetime.now().astimezone(), time.monotonic()
        fetches = []
        token = _fetches.set(fetches)
        record = {"call_id": uuid.uuid4().hex, "started": started.isoformat(timespec="seconds"),
                  "tool": context.message.name, "arguments": context.message.arguments or {},
                  "outcome": "ok", "error": None, "results": None}
        try:
            result = await call_next(context)
            record["results"] = _result_count(result)
            return result
        except Exception as e:
            record.update(outcome="error", error={"type": type(e).__name__, "message": str(e)[:500]})
            raise
        finally:
            _fetches.reset(token)
            record.update(duration_ms=round((time.monotonic() - clock) * 1000), fetches=fetches,
                          client=_client(context), ukdatapy_version=_version(),
                          server_path=SERVER_PATH, git_sha=GIT_SHA, git_dirty=GIT_DIRTY,
                          python=platform.python_version(), pid=os.getpid())
            try:
                paths.append_jsonl(paths.cache_dir() / f"calls_{started:%Y-%m}.jsonl", record)
            except OSError as e:
                print(f"ukdatapy: call not logged: {e}", file=sys.stderr)
