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
uv run tracker pull comptroller             # Comptroller data center registry (owners, tenants)
uv run tracker pull abatements              # Chapter 312 tax abatements for data centers
uv run tracker pull tceq-registry           # TCEQ Central Registry: RNs, stormwater, air IDs
uv run tracker sites                        # sites, permits, companies, NSR refs
uv run tracker sites --linked               # only sites with two or more kinds of signal
uv run tracker equipment                    # current units per site, each counted once
uv run tracker equipment --all              # every row from every document
uv run tracker owners lookup                # franchise tax details and sister LLCs
uv run tracker owners queue                 # entities still without a company
uv run tracker ticker ORCL                  # one ticker's sites and supplied equipment
uv run tracker refresh                      # all collectors + change report (weekly job)
uv run tracker schedule install             # run refresh every Monday 06:00 via launchd
uv run tracker db rebuild                   # re-extract saved documents after an upgrade
uv run pytest
```

Web app (ticker pages):

```bash
cd web && npm install && npm run build && cd ..
uv run tracker serve                        # http://127.0.0.1:8000
```

For frontend work, run `tracker serve` and `npm run dev` in `web/` together; Vite proxies
`/api` to port 8000.

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

## Comptroller registry

`tracker pull comptroller` reads the Texas Comptroller's
[list of data centers certified for the state sales tax exemption](https://comptroller.texas.gov/taxes/data-centers/data-center-lists.php):
each entry's owner, occupant, and operator. Every party becomes an entity linked to the
site, and known companies are tied to their tickers (exact legal names such as
"Oracle America Cloud Services, LLC" are high confidence; brand names in an LLC's name are
medium). The registry gives no location, so an entry joins an existing site only on a unique
entity match (a party is also a TCEQ permit holder there), a unique name match, or as a later
phase of a matched site that shares a party. Those company links are medium confidence,
because the tie between the registry entry and the site is. Weaker candidates are saved as
low-confidence links for review, never merged. Rerunning reports new, changed, and dropped
entries.

## Tax abatements

`tracker pull abatements` downloads the Comptroller's
[Chapter 312 abatement file](https://comptroller.texas.gov/economy/development/search-tools/ch312/abatements-simple.php)
(official CSV, one row per taxing unit) and keeps agreements that are data centers: a data
hosting NAICS code (5182), "data center" in the zone or owner name, an owner already tied to
a tracked site, or an owner from a data-center-only company (Crusoe, Compass, Equinix,
miners). Amazon or other hyperscaler owners alone don't count, since they also get
abatements for warehouses and offices.

Each agreement becomes an APPROVAL signal and its organization owners OWN the site. It is
matched to a site by owner (an owner tied to exactly one site), then by owner and place
when the owner has several sites ("Houston City" picks CyrusOne's Houston site). An owner on
several sites with no place to decide leaves the signal unmatched with low-confidence review
links. A match also fills in the county, which registry sites lack. Owners that look like
individuals, and the local official who filed the report, are never stored.

## TCEQ Central Registry

`tracker pull tceq-registry` queries TCEQ's Central Registry files on data.texas.gov (five
regional datasets, official open data, updated daily). Each row is one regulated site (RN)
and one program ID at it. The pull asks for data center rows (data hosting industry code,
or "data center" in the site name or description) plus every row for RNs already tracked.

A site counts as a data center if it is named as one, or has a data hosting code and a known
company as principal (the code alone also covers corporate server rooms and print shops).
Concrete batch plants, substations, conduits, and laydown yards that carry a data center's
name are skipped.

Each RN is matched to a site by RN, then by distinctive name in a compatible county, then by
principal (an entity tied to the site by another source; contractors and utilities never
count), else it becomes its own site. Construction and industrial stormwater authorizations
become STORMWATER signals; air NSR IDs not already known from a permit document become
AIR_PERMIT signals. An active construction stormwater authorization from the last three
years moves an early-stage site to under construction. Rows whose principal is an individual
are dropped before the snapshot is saved.

## Permit holders

Each TCEQ filing's applicant becomes an entity linked to its site as permit holder
(high confidence: the filing names it for that site), and to a known company when its name is
one ("Microsoft Corporation" -> MSFT, high; "Vantage Data Centers Tx11, LLC" -> Vantage,
medium). The same legal entity is one record however a filing spells it, and a name cut off
at a line break joins its full name. Only organizations become entities: a name with no
company marker (LLC, Inc., Association, ...) is skipped, so no person's name is stored.
`tracker sites` lists each site's permit holders and companies.

## Owner lookups

Project LLCs ("Nolan Ranch DC Owner LLC") rarely say who is behind them. Three steps:

1. `tracker owners lookup` looks up every site entity with no confirmed company in the
   Comptroller's [Active Franchise Taxpayers](https://data.texas.gov/dataset/Active-Franchise-Taxpayers/9cir-efmm)
   file (official download, free): mailing address, SOS file number, formation date.
2. It then lists the other taxpayers at each mailing address. Sister LLCs often share one
   ("SF HOUC" and "SF HOUH" at an El Segundo PO box). They are linked as affiliates: medium
   when the first word of the name matches, low otherwise. An address with more than 25
   taxpayers is a registered agent's office and is ignored, unless the address is a known
   company's own: two or more taxpayers there carry its name and none carries another known
   company's (Polmer LLC at Meta's 1601 Willow Rd). That gives a medium company link.
3. `tracker owners queue` lists what is still unknown and the next hand step, usually an
   SOSDirect lookup by file number (paid, done by hand, never scraped). Record what you find
   in `seeds/owner_findings.csv` with an evidence URL, then run `tracker owners apply`.
   `--csv data/owner_queue.csv` writes the list; sites with an air permit come first.
   Findings are re-applied on every `db rebuild`. Companies only: the file rejects names
   that are not organizations, and no person's name belongs there.

Every lookup response is saved like any other raw document, so `db rebuild` replays them
without downloading.

## Weekly refresh

`tracker refresh` runs TCEQ discovery, the Comptroller registry pull, the Chapter 312
abatement pull, the TCEQ Central Registry pull, and owner lookups for new entities, in that
order. A source that fails is recorded and the others still run. It
writes `data/refresh/<timestamp>.md` (and `.json`), copied to `data/refresh/latest.md`:

- new filings, new sites, status changes, and companies newly tied to a site;
- health checks: a source that returns nothing, or under half of last run's count, or a
  burst of dropped registry entries or failed downloads, is flagged instead of looking
  like a quiet week. The command exits with status 1 when a check fails.

`tracker schedule install [--day mon --at 06:00]` installs a launchd LaunchAgent
(`~/Library/LaunchAgents/com.texas-dc-tracker.refresh.plist`) that runs the refresh weekly;
a Mac that is asleep at that time runs it on the next wake. Output goes to
`data/refresh/launchd.log`. `tracker schedule status` shows whether it is loaded and the
latest report; `run-now` starts it immediately in launchd's environment; `uninstall`
removes it.

macOS keeps background jobs out of `~/Downloads`, `~/Documents`, and `~/Desktop`. If the
project lives there, grant uv Full Disk Access (System Settings > Privacy & Security) or
move the project, then check with `tracker schedule run-now`.

## Site pages

`tracker serve` runs a JSON API (docs at `/api/docs`) and the React app in `web/`. It is
read-only except for review decisions (below); keep it on 127.0.0.1.
The app opens on the **feed** (`/api/feed`): filings newest first by the date on the
document, each tagged by signal type and linked to its site, with the companies behind the
site and the generators and turbines that filing lists. Windows of 7, 30, 90, 365 days or
all (keys `1`-`5`), a signal type filter, and `j`/`k`, `enter` (open site), `d` (open
document).

**Map + sites** (`#/sites`) puts every located site on a Texas county map, sized by permitted
MW and colored by status, beside the site table; filters apply to both. Filings rarely give
coordinates, so positions are approximate: a site sits at the city its TCEQ records name
(Census Gazetteer place point) or, failing that, at its county's center (drawn as a ring).
Sites named only in the Comptroller registry give no place and appear in the table only.
`scripts/build_texas_reference.py` builds the county shapes and place points from official
Census data (run once; its outputs are committed).

The site list (`/api/sites`) has every site with signals, sortable by status,
signal kinds, equipment, MW, and latest filing, and filterable by county, status, company
or ticker, and "2+ kinds of signal". Companies are shown at medium or high confidence only.

A site page (`/api/sites/{id}`) shows:

- **Owner chain:** each legal entity named on a filing at the site, the role it was named in
  (owner, tenant, operator, permit holder) and the filing, then the company behind it and
  why, plus sister entities found at the same franchise tax address. Every step shows its
  confidence and a source link; the chain is as strong as its weaker step.
- **Timeline:** every signal, newest first, with its source and how it was tied to the site
  (its own RN or permit number, or a name or owner match with confidence).
- **Equipment:** the netted unit counts per permit, with make, model, size, and the page.
- **Open questions:** low-confidence "same site?" links waiting for review.

Entity officers and registered agents are never returned.

## Supplier scoreboard

`#/suppliers` (`/api/suppliers`) ranks manufacturers by permitted generators and turbines,
by MW or unit count, with a column per quarter. Counts are the same netted units as the
site and ticker pages. A unit's quarter is when its permit was first filed (the earliest
dated document for that permit at the site), the closest public sign of when it was
ordered. Shares are of units whose make the permit names; most permits name none, so the
"Make not stated" row is shown and shares read as a sample. Click a manufacturer for its
models, sites, and the permit page behind every count.

## Review queue

The importers leave a low-confidence link wherever a match is plausible but not settled.
`#/review` (`GET /api/review`) lists them in four tabs:

- **Same site?** Two sites that may be one project. Confirm merges them: the site with
  fewer signals moves into the other (signals, equipment, links, RNs, its name as an alias,
  the further-along status), and its old page points to the merged one.
- **Filing → site.** A filing whose owner has several sites (the Riot abatement in Navarro
  County, the Galaxy one in Dickens). Confirm moves the filing to that site, adds its owners,
  and rejects the other candidates.
- **Parent company.** A company that may be behind an entity, from a shared mailing address.
- **Sister LLCs.** Entities at the same franchise tax address.

Keys: `j`/`k` move, `y` confirm, `n` reject, with an optional note. A confirm adds a Link with
method `manual` and high confidence; a reject hides the guess from every view (links are
never deleted). Each decision also goes to `seeds/review_decisions.csv`, keyed by RN, site
name, filing number, or legal name rather than database ids, and `tracker db rebuild`
replays it. That file is committed: notes should name companies and sources, never people.

## Ticker pages

The ticker index (`/api/tickers`) lists every ticker with sites or supplied equipment. A
ticker page (`/api/tickers/{symbol}`) has two sides:

- **Sites (demand):** every site where an entity belonging to the company is owner, tenant,
  operator, or permit holder, with confidence. Click a row for the chain of evidence: which
  filing names the entity at the site, and why the entity belongs to the company. Generator
  and turbine counts and MW are each site's netted inventory, linked to the permit they come
  from. MW shown as "≥" means some units have no size in any document.
- **Equipment supplied:** generators and turbines by makers under that ticker (Caterpillar,
  Solar Turbines, and Perkins all count for CAT), each unit counted once, with permit and page.

Low-confidence ties are listed as leads and left out of every total.

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
  collectors/tceq/       TCEQ collectors (seed list, Records Online, Central Registry)
  rollup.py              current equipment per site, each unit counted once
  registry_import.py     Comptroller registry: site matching and party links
  abatement_import.py    Chapter 312 abatements: data center filter, site matching
  central_registry_import.py  TCEQ Central Registry: RN matching, stormwater and air IDs
  entities.py            entities, entity-to-site and company links, confidence rollup
  owners.py              franchise tax lookups, sister LLCs, hand findings
  views/ticker.py        ticker page data: demand-side sites, supplied equipment
  views/sites.py         site list and site page: owner chain, timeline, equipment
  views/feed.py          feed: filings newest first with site, companies, equipment
  views/suppliers.py     supplier scoreboard: units by manufacturer and quarter
  locate.py              approximate site positions (city point or county center)
  review.py              review queue: confirm/reject low links, merges, decision replay
  jobs.py                collection jobs shared by the CLI and the refresh
  refresh.py             weekly refresh: run collectors, change report, health checks
  schedule.py            launchd LaunchAgent for the weekly refresh
  api.py                 FastAPI JSON API, serves web/dist
web/                     React + Vite frontend
  collectors/comptroller/ Comptroller registry, Chapter 312 abatements, franchise tax file
  reference/companies.py companies behind legal names, with tickers
  parsers/pdf.py         pdfplumber with PyMuPDF fallback
  extract/tceq_air.py    rule-based equipment extraction
  reference/suppliers.py manufacturers and tickers
  reference/tx_*.csv     Texas county centers and Census place points
scripts/build_texas_reference.py  builds those and web/src/texas-map.json
```

## License

MIT
