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
uv run tracker equipment                    # counts per site
uv run pytest
```

Data goes to `data/` (git-ignored). Override with `TRACKER_DATA_DIR` or `TRACKER_DB_URL`.

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
  collectors/tceq/       TCEQ collectors
  parsers/pdf.py         pdfplumber with PyMuPDF fallback
  extract/tceq_air.py    rule-based equipment extraction
  reference/suppliers.py manufacturers and tickers
```

## License

MIT
