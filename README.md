# Texas Data Center Tracker

Links every public filing about a Texas data center to one site record, then to the
public companies that own it, rent it, and supply it.

> **Not investment advice.** This project compiles public records for research. Links
> between sites and companies carry confidence levels and can be wrong. Do not treat
> anything here as a recommendation to buy or sell a security.

## Status

Phase 1: TCEQ air permits. Collects permit documents, saves each raw file with a SHA-256
hash, and extracts generator and turbine counts, unit sizes, fuel, and make/model into SQLite.

Pilot counties: Taylor, Milam, Bexar, Tarrant, Ellis, Hunt, Ward.

## Quick start

```bash
uv sync
uv run tracker db init
uv run tracker ingest path/to/permit.pdf    # files you downloaded by hand
uv run tracker pull tceq                    # URLs listed in seeds/tceq_air.csv
uv run tracker discover tceq --dry-run      # search TCEQ Records Online, list what it would fetch
uv run tracker discover tceq                # fetch and ingest those documents
uv run tracker sites                        # sites, permits, unit groups, NSR refs
uv run tracker equipment                    # current units per site, each counted once
uv run tracker equipment --all              # every row from every document
uv run tracker db rebuild                   # re-extract saved documents after an upgrade
uv run pytest
```

Data goes to `data/` (git-ignored). Override with `TRACKER_DATA_DIR` or `TRACKER_DB_URL`.
There are no migrations yet: after a schema change, delete `data/tracker.db` and pull again.

## Discovery

`tracker discover tceq` searches [TCEQ Records Online](https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_SEARCH)
for air permits whose site name contains DATA CENTER, DATACENTER, or DATA CAMPUS
(change with `--keyword`). For each permit it lists every document and fetches the
technical review and final action from the most recent project that has a review.
Documents already in the database are skipped, so it is safe to rerun weekly.

Limits: sites not named like a data center are missed (for example a campus filed under a
shell LLC's project name), and the search is statewide, so the county is only known after a
document is parsed. Concrete batch plants named after a data center are skipped.

After an extractor or schema change, `tracker db rebuild` re-extracts every saved document
into a fresh database without downloading anything.

## How counts are netted

Permits describe the same units several times and mix existing, new, and removed units.
Each equipment row is tagged with what its sentence says (`total`, `existing`, `added`,
`removed`, `subset`, or `unknown`), restatements of the same units are merged, and
`tracker equipment` shows the net per site: `+` counted, `-` subtracted, blank not counted.
Withdrawn applications are excluded. Every row keeps the sentence it came from, so a
surprising total can be checked against the document.

## How it stays trustworthy

- Raw documents are stored as downloaded, named by SHA-256, so every number traces to a file.
- Extracted fields live in their own table, tagged with the extractor version, so a better
  extractor can re-run over old filings.
- Every equipment row keeps the page number and the sentence it came from.
- Every link between records carries a method, a confidence level, and evidence.
- Site status changes are logged with dates.

## Collection rules

- Official downloads and APIs before scraping.
- robots.txt is honored; at most 1 request every 2 seconds per host.
- Never logs in anywhere. Paid lookups (SOSDirect) are done by hand, not scraped.
- Names of private individuals (LLC officers, agents) stay out of the public app and repo.

## Layout

```
src/tracker/
  cli.py                 `tracker` command
  http.py                rate-limited, robots-aware client
  storage.py             raw file storage by hash
  ingest.py              document -> signal -> extraction -> equipment
  db/models.py           schema
  collectors/tceq/       TCEQ collectors (seed list, Records Online discovery)
  rollup.py              current equipment per site, each unit counted once
  parsers/pdf.py         pdfplumber with PyMuPDF fallback
  extract/tceq_air.py    rule-based equipment extraction
  reference/suppliers.py manufacturers and tickers
```

## License

MIT
