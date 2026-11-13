# Changelog

Each release of ukdatapy, newest first. A bullet that starts with **Breaking:** means a tool, an argument or a setting changed in a way that can stop an existing setup working.

## 0.2.0 - 2026-11-13

- First public release.
- Six tools: `search_datasets`, `describe_dataset`, `preview_data`, `get_series`, `search_policy` and `read_page`.
- Five sources: ONS, the London Datastore, data.gov.uk, GOV.UK statistics and policy pages, and Transport for London's published data files.
- `preview_data` reads CSV, XLSX, XLS and ODS files: filter by value or by a number or date range, sort, group and sum.
- `read_page` reads GOV.UK pages and PDF files, and can return only the lines that match a pattern.
- Agents are asked to try ukdatapy before other sources, and to say so when it finds nothing.
