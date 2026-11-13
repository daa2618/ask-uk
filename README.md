# ukdatapy

An MCP server that lets AI agents find and read UK open data from five sources:

| Source | `source` value | What it covers |
|---|---|---|
| Office for National Statistics | `ons` | Search across ons.gov.uk, dataset pages and their files, time series (CPI, unemployment, GDP growth and others) |
| London Datastore | `london` | The Greater London Authority catalogue and its files |
| data.gov.uk | `datagovuk` | The national catalogue of UK government datasets and their files |
| GOV.UK | `govuk` | Statistics pages on www.gov.uk and their attached files: departments' current tables, such as the Home Office immigration statistics. Also policy pages, through `search_policy` and `read_page` |
| Transport for London | `tfl` | TfL's published data files: station entries, exits and footfall, rail and bus demand, cycle hire journeys and cycle counts. Each folder is a dataset, such as `crowding/Network Demand`. The live TfL API is not included |

## Tools

| Tool | What it does |
|---|---|
| `search_datasets` | Searches all five catalogues, or only the ones you name. GOV.UK hits come newest first, each with its relevance score and rank, so an older page that matches better still shows. |
| `describe_dataset` | Returns a dataset's metadata and its files, each with a resource `id`. A dataset with more than 100 files lists the 100 newest, plus `resources_total`. |
| `preview_data` | Reads rows from one CSV, XLSX, XLS or ODS file: filter, sort, pick columns, page. At most 200 rows per call. `group_by` and `sum` return totals and row counts over every matching row. |
| `get_series` | Reads an ONS time series by its page path, by frequency and year range. |
| `search_policy` | Searches GOV.UK policy pages: ministers' statements, consultations and outcomes, policy papers (including changes to the Immigration Rules), news, guidance. Each hit carries its page `type`. |
| `read_page` | Reads one GOV.UK page as plain text, 20,000 characters per call, with its dates and attachments. With `attachment`, it reads a PDF or HTML attachment instead, each PDF page marked `[page N]`. With `find`, a case-insensitive regular expression, it returns only the matching lines from the whole text, each with one line either side, its `offset` and its PDF page. With another `source`, it reads a PDF file from that source's dataset: pass the dataset id as `path` and the file id as `attachment`. |

Every search hit, dataset and file carries `age_days`: the days since its catalogue's last update date, or null when the catalogue gives none. For data.gov.uk, that date is when the catalogue entry last changed, which can be later than the data.

## Security

- The server fetches only URLs on `ukdatapy/allowlist.toml`: any `*.gov.uk` host over https, plus two named cloud buckets used by DfT and Defra.
- Each redirect hop is checked against the same list.
- A connection to a loopback, private or link-local address (such as `169.254.169.254`) is refused, even for a host on the list. The check runs on the connected socket, so a DNS answer that changes after a lookup is caught too.
- URLs with backslashes, whitespace, control characters or a port other than 443 are refused, and urllib3 must see the same host as Python's URL parser.
- `preview_data` takes a dataset id and a resource id, never a URL. The URL comes from the publisher's catalogue.
- Every response body, including catalogues and pages, stops at 100 MB. A preview reads at most 300,000 rows, or 30 million cells, of one file. An XLSX or ODS file that unpacks to more than 2 GB is refused.
- To use another allowlist, set `UKDATAPY_ALLOWLIST` to the path of a TOML file in the same format.

## Install and connect

Needs Python 3.12 or later.

1. Install. This creates a program, `ukdatapy-mcp`, in the `.venv` folder. You never run it yourself: your agent starts it in the background when it needs the tools.

   With pip (no other tools needed):

   ```bash
   git clone https://github.com/daa2618/ask-uk && cd ask-uk
   python3 -m venv .venv
   .venv/bin/pip install .
   ```

   Or, if you have [Poetry](https://python-poetry.org/) and want to run the tests or change the code:

   ```bash
   git clone https://github.com/daa2618/ask-uk && cd ask-uk
   POETRY_VIRTUALENVS_IN_PROJECT=true poetry install
   ```

   `POETRY_VIRTUALENVS_IN_PROJECT=true` puts the environment in `.venv`, so the next steps are the same for both.

2. Print the program's full path. Your agent starts the program from its own folder, so it needs the full path, not `.venv/bin/...`.

   ```bash
   realpath .venv/bin/ukdatapy-mcp
   ```

   It prints something like `/home/me/ask-uk/.venv/bin/ukdatapy-mcp`. On Windows, the program is `.venv\Scripts\ukdatapy-mcp.exe`.

3. Register the program with your agent, once, using that path. For Claude Code:

   ```bash
   claude mcp add -s user ukdatapy -- /home/me/ask-uk/.venv/bin/ukdatapy-mcp
   ```

   `-s user` makes it available in every folder. Without it, Claude Code uses it only in the folder you ran the command from. For other agents, see the table below.

4. Start a new session and ask a question about UK data, such as "What is the latest UK inflation rate?". The agent calls the ukdatapy tools when it needs them. In Claude Code, `/mcp` lists the connected servers.

### Claude Code inside the `ask-uk` folder

`ask-uk` includes two Claude Code files, `.mcp.json` and `.claude/settings.json`. When you start Claude Code inside the `ask-uk` folder:

- It starts ukdatapy from this folder's `.venv`, so you can skip step 3 there. The first time, Claude Code asks you to approve the `ukdatapy` server: accept it. Until you do, the session has no ukdatapy tools.
- Web search and web fetch are turned off. Every answer comes from ukdatapy, or says that ukdatapy found nothing, which shows what ukdatapy covers. Start Claude Code in any other folder to keep your web tools.
- `.mcp.json` uses the macOS and Linux path. On Windows, change `command` to `.venv\\Scripts\\ukdatapy-mcp.exe`.

## Other agents

Step 3 for each agent. Replace the path with the one step 2 printed. The program needs no arguments, keys or accounts.

| Agent | Register with |
|---|---|
| Claude Code | `claude mcp add -s user ukdatapy -- /path/to/ask-uk/.venv/bin/ukdatapy-mcp` |
| Claude Desktop | Edit `claude_desktop_config.json`: see [Claude Desktop](#claude-desktop) below. |
| Gemini CLI | `gemini mcp add -s user ukdatapy /path/to/ask-uk/.venv/bin/ukdatapy-mcp` |
| Codex CLI | `codex mcp add ukdatapy -- /path/to/ask-uk/.venv/bin/ukdatapy-mcp` |
| Any other MCP client | Add a stdio server whose command is `/path/to/ask-uk/.venv/bin/ukdatapy-mcp`. |

### Claude Desktop

1. Open the config file. In the Claude menu in your system's menu bar (not the settings inside the chat window), choose Settings, then Developer, then **Edit Config**. This creates the file if it does not exist. It lives at:

   | System | File |
   |---|---|
   | macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
   | Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
   | Linux | `~/.config/Claude/claude_desktop_config.json` |

2. Add the `ukdatapy` entry inside `"mcpServers"`. If the file already lists other servers, keep them: add `ukdatapy` next to them, with a comma between entries. Do not replace the whole file.

   ```json
   {
     "mcpServers": {
       "ukdatapy": {
         "command": "/path/to/ask-uk/.venv/bin/ukdatapy-mcp"
       }
     }
   }
   ```

   On Windows, write the path with double backslashes, such as `"C:\\Users\\me\\ask-uk\\.venv\\Scripts\\ukdatapy-mcp.exe"`.

3. Quit Claude Desktop completely and start it again. Closing the window is not enough. The server then shows under the **+** button in the message box, in Connectors.

If it does not appear, read `mcp-server-ukdatapy.log` in Claude's log folder: `~/Library/Logs/Claude` on macOS, `%APPDATA%\Claude\logs` on Windows, `~/.config/Claude/logs` on Linux.

### Local models with Ollama

Ollama runs the model but cannot call MCP tools by itself. Use an MCP client that drives Ollama, such as [ollmcp](https://github.com/jonigl/mcp-client-for-ollama):

```bash
pip install ollmcp
ollmcp mcp add ukdatapy -- /path/to/ask-uk/.venv/bin/ukdatapy-mcp
ollmcp --model qwen3.5
```

Pick a model that supports tool calls. Small models handle the search, describe and preview steps less reliably than large ones.

### Clients that need a URL

Some clients connect to a URL instead of starting a command. Serve ukdatapy over HTTP on your own machine:

```bash
.venv/bin/fastmcp run ukdatapy/server.py:mcp --transport http --host 127.0.0.1 --port 8765
```

Then point the client at `http://127.0.0.1:8765/mcp`. The server has no login, so keep it on `127.0.0.1`. Claude on the web (claude.ai) cannot use this: it connects from Anthropic's servers, not from your machine.

## Updating

Update your copy once. Every agent points at the same program, so you do not register it again.

1. Inside `ask-uk`, get the new version and install it:

   | Installed with | Command |
   |---|---|
   | pip | `git pull && .venv/bin/pip install .` |
   | Poetry | `git pull && poetry install` |

   `git pull` brings the latest release. For one particular release, run `git fetch --tags && git checkout v0.3.0` (with the version you want) before the install command.

2. Restart each agent you use. A running agent keeps the old version until it starts the program again.

   | Agent | What to do |
   |---|---|
   | Claude Code | Run `/mcp` and reconnect `ukdatapy`, or start a new session |
   | Claude Desktop | Quit it completely and start it again |
   | Gemini CLI, Codex CLI, ollmcp | Start a new session |

To hear about new releases, use **Watch > Custom > Releases** on the repository page.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `UKDATAPY_CACHE_DIR` | `~/.cache/ukdatapy` | Daily cache of catalogues and downloaded files. Not read from `.env`, so a repo you open cannot redirect it. |
| `UKDATAPY_ALLOWLIST` | `ukdatapy/allowlist.toml` | The allowlist file. |

Your agent starts the server, and the server gets the variables saved in its registration plus those of the terminal the agent started in. There are two ways to set one.

To save it in the registration, so every session gets it, pass `-e` when you add the server. Put the name `ukdatapy` before `-e`, or `-e` takes it as a second variable. To change an existing registration, remove it first:

```bash
claude mcp remove -s user ukdatapy
claude mcp add -s user ukdatapy -e UKDATAPY_CACHE_DIR=/path/to/my_cache -- /path/to/ask-uk/.venv/bin/ukdatapy-mcp
```

`claude mcp get ukdatapy` then lists it under `Environment:`. Other agents have an `env` block in their server config for the same purpose.

To set it for one terminal only, export it, then start your agent in that terminal:

```bash
export UKDATAPY_ALLOWLIST=~/my_allowlist.toml
claude
```

Either way, a server that is already running keeps its old value until it restarts: in Claude Code, run `/mcp` and reconnect ukdatapy.

## Audit log

Every tool call adds one JSON line to `<UKDATAPY_CACHE_DIR>/calls_<YYYY-MM>.jsonl`, whichever agent made the call.

| Field | Holds |
|---|---|
| `call_id`, `started`, `duration_ms` | A random id, local start time with offset, time taken. |
| `tool`, `arguments` | The tool and the arguments the agent sent. |
| `outcome`, `error` | `ok` or `error`; the error's type and message. |
| `fetches` | Each HTTP request: `url`, `outcome` (`ok`, `redirect`, `blocked`, `http_error`, `too_large`, `failed`), `status`, `bytes`, `ms`, `error`. Files served from the daily cache have no fetch. |
| `client` | The MCP client's name and version, such as `claude-code`. |
| `results` | For `search_datasets` and `search_policy`, how many hits came back; empty for other tools. `0` means the search found nothing at all. A non-zero count can still be all off-topic: GOV.UK search always returns its closest matches. |
| `ukdatapy_version`, `server_path`, `python`, `pid` | The server that answered. `server_path` is its installed package folder, which tells an `ask-uk` clone's server from another install. |
| `git_sha`, `git_dirty` | When the server runs from a git checkout (`poetry install`), the commit it started on and whether the package folder had uncommitted changes. Empty for a pip install, whose copy has no history. |

- The user's prompt is not recorded. MCP does not send it to servers.
- A log that cannot be written prints one warning on stderr. The tool call still answers.
- Clearing the cache folder deletes the log too.

```bash
jq -c '{tool, outcome, hosts: [.fetches[].url | split("/")[2]]}' ~/.cache/ukdatapy/calls_*.jsonl
```

## Tests

```bash
poetry run pytest -q
```

Tests run offline: sockets are blocked and every HTTP call goes through a fake with recorded fixtures.
