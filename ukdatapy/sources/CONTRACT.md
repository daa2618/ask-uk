# Source adapter contract

Each source is one module: `ukdatapy/sources/ons.py`, `london.py`, `datagovuk.py`. The MCP server only calls these functions.

| Function | Returns |
|---|---|
| `search(query: str, limit: int = 10) -> list[dict]` | Up to `limit` hits, best first. Each hit: `source`, `id`, `title`, `summary` (at most 300 characters), `url`, `updated` (`YYYY-MM-DD` or `""`). |
| `describe(dataset_id: str) -> dict` | `source`, `id`, `title`, `description` (at most 1000 characters), `publisher`, `updated`, `url`, `resources`. |

- Each resource: `id`, `name`, `format` (upper case, like `CSV`, `XLSX`, `ZIP`, or `""`), `url`, `updated`.
- A resource `id` is unique within its dataset and stays the same between calls on the same day.
- An unknown `dataset_id` raises `LookupError`.
- `source` is the module's `SOURCE` constant: `"ons"`, `"london"` or `"datagovuk"`.

Rules:
- Make every request with `ukdatapy.core.http.get` or `ukdatapy.core.http.download`. Never import `requests` in a source.
- Never print to stdout. Under the stdio transport, stdout carries the MCP protocol.
- Use `ukdatapy.core.paths.load_daily_json` for any catalogue fetched whole (the London export).
- Tests run offline with the `fake_http` fixture and recorded fixtures in `tests/fixtures/<source>/`. Trim the fixtures to a few KB.
