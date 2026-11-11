"""TCEQ Records Online: find air permit documents for data centers without hand-picked URLs.

Records Online (records.tceq.texas.gov) is TCEQ's public document search. There is no
login. The search page asks a public endpoint for an access ID it logs searches under, then
runs the search. This module does the same, leaving out the third-party IP lookup the
page's JavaScript makes (the field is sent blank, as it is when that lookup fails).

Discovery is two steps:

1. Search site names ("Regulated Entity Name") for keywords like DATA CENTER. That finds
   permit numbers. It can't find every document, because technical reviews are filed with
   the site name left blank.
2. For each permit number, list all of its documents and pick the ones worth parsing: the
   technical review and final action from the most recent project that has a review.

Result pages are HTML tables. The parser reads the header row for column positions, so a
reordered table still parses, and a missing column raises instead of reading the wrong one.
"""

from __future__ import annotations

import html as html_lib
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode

from tracker.http import PoliteClient

BASE_URL = "https://records.tceq.texas.gov/cs/idcplg"
ACCESS_URL = f"{BASE_URL}?IdcService=TCEQ_SEARCH_ADD_ACCESS&clientIP=&searchType=External&IsJson=1"

# Record series IDs from the search form's "Record Series" list.
NSR_PERMIT = "1081"  # AIR / New Source Review Permit (also holds standard permits and PBRs)
FEDERAL_OPERATING_PERMIT = "1051"  # AIR / Federal Operating Permit (Title V)

DEFAULT_KEYWORDS: tuple[str, ...] = ("DATA CENTER", "DATACENTER", "DATA CAMPUS")
PAGE_SIZE = 100
# Document types worth parsing, in the order they're preferred.
WANTED_TYPES: tuple[str, ...] = ("Technical Review", "Final Action")

_REQUIRED_COLUMNS = ("Content ID", "Primary ID", "Secondary ID", "Title", "Begin Date")


class ResultsFormatError(Exception):
    """The results page didn't look like a Records Online results table."""


@dataclass(frozen=True)
class RecordRow:
    d_id: str  # download id
    content_id: str
    primary_id: str  # permit / registration number
    secondary_id: str | None  # project number
    title: str  # document type shown as "Title", e.g. "Technical Review"
    begin_date: date | None
    entity_name: str | None  # regulated entity (site) name, often blank on reviews

    @property
    def file_url(self) -> str:
        return f"{BASE_URL}?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&dID={self.d_id}&Rendition=Web"


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _parse_date(text: str) -> date | None:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date()
    except ValueError:
        return None


def parse_results(page: str) -> tuple[list[RecordRow], int]:
    """Rows on this page and the total number of matches across all pages."""
    total_m = re.search(r"(?:Potential items \d+-\d+ of|Found)\s+([\d,]+)", page)
    if total_m is None:
        if re.search(r"No (?:results|items) found|Found 0 ", page, re.IGNORECASE):
            return [], 0
        raise ResultsFormatError("no result count on page")
    total = int(total_m.group(1).replace(",", ""))

    headers = [
        _clean(h)
        for h in re.findall(
            r'<t[hd][^>]*class="xuiListHeaderCell[^"]*"[^>]*>(.*?)</t[hd]>', page, re.S
        )
    ]
    missing = [c for c in _REQUIRED_COLUMNS if c not in headers]
    if total and missing:
        raise ResultsFormatError(f"missing columns: {missing}")
    col = {name: i for i, name in enumerate(headers)}

    rows: list[RecordRow] = []
    for raw in re.findall(r'<tr><td scope="row".*?</tr>', page, re.S):
        d_id = re.search(r'name="dID" value="(\d+)"', raw)
        if d_id is None:
            continue  # folders have no file
        cells = [
            _clean(c)
            for c in re.findall(
                r'<td[^>]*class="xuiListContentCell_[^"]*"[^>]*>(.*?)</td>', raw, re.S
            )
        ]
        if len(cells) < len(headers):
            continue

        def cell(name: str, cells: list[str] = cells) -> str:
            return cells[col[name]] if name in col else ""

        rows.append(
            RecordRow(
                d_id=d_id.group(1),
                content_id=cell("Content ID"),
                primary_id=cell("Primary ID"),
                secondary_id=cell("Secondary ID") or None,
                title=cell("Title"),
                begin_date=_parse_date(cell("Begin Date")),
                entity_name=cell("Regulated Entity Name") or None,
            )
        )
    return rows, total


def _query_text(series: str, field: str, value: str) -> str:
    value = value.replace("`", "")
    return (
        f"dSecurityGroup <matches> `Public`<AND>xRecordSeries<matches>`{series}`"
        f"<AND>({field} <substring> `{value}`)"
    )


class RecordsOnline:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    def _access_id(self) -> str:
        data = self.client.get(ACCESS_URL).json()
        return str(data["LocalData"]["accessID"])

    def search(
        self, field: str, value: str, *, series: str = NSR_PERMIT, max_rows: int = 2000
    ) -> Iterator[RecordRow]:
        """Every matching document, newest first, following result pages."""
        access = self._access_id()
        start = 1
        while start <= max_rows:
            params = {
                "IdcService": "TCEQ_PERFORM_SEARCH",
                "QueryText": _query_text(series, field, value),
                "SortField": "dInDate",
                "SortOrder": "Desc",
                "ResultCount": str(PAGE_SIZE),
                "SearchEngineName": "ELASTICSEARCH",
                "SearchQueryFormat": "UNIVERSAL",
                "searchFormType": "standard",
                "listTemplateId": "SearchResultsHeadline",
                "IsExternalSearch": "1",
                "accessID": access,
                "PageNumber": str((start - 1) // PAGE_SIZE + 1),
                "StartRow": str(start),
                "EndRow": str(start + PAGE_SIZE - 1),
            }
            rows, total = parse_results(self.client.get(f"{BASE_URL}?{urlencode(params)}").text)
            yield from rows
            start += PAGE_SIZE
            if not rows or start > total:
                return

    def permits_by_name(
        self, keywords: Iterable[str], *, since: date | None = None
    ) -> dict[str, str | None]:
        """Permit number -> site name, for sites whose name contains any keyword."""
        found: dict[str, str | None] = {}
        for kw in keywords:
            for row in self.search("xRegEntName", kw):
                if not row.primary_id or (since and row.begin_date and row.begin_date < since):
                    continue
                if row.primary_id not in found or (row.entity_name and not found[row.primary_id]):
                    found[row.primary_id] = row.entity_name
        return found

    def permit_documents(self, permit_number: str) -> list[RecordRow]:
        # Substring search, so keep exact matches only (177262 must not match 1772620).
        return [
            r for r in self.search("xPrimaryID", permit_number) if r.primary_id == permit_number
        ]


# Sites named after a data center they help build, not the data center itself.
CONSTRUCTION_SITE_RE = re.compile(
    # "CBP" is TCEQ shorthand for concrete batch plant ("CEMCO-367 & 354 CBP POOLSIDE ...").
    r"\b(?:BATCH\s+PLANT|CBP|CEMCO|CONCRETE|REDI-?MIX|READY-?MIX|ASPHALT|ROCK\s+CRUSH)",
    re.IGNORECASE,
)


@dataclass
class PermitPlan:
    permit_number: str
    site_name: str | None
    documents: list[RecordRow]


@dataclass
class DiscoveryPlan:
    permits: list[PermitPlan]
    skipped_construction: list[tuple[str, str | None]]


def plan_discovery(
    ro: RecordsOnline,
    keywords: Iterable[str] = DEFAULT_KEYWORDS,
    *,
    since: date | None = None,
    max_permits: int = 0,
) -> DiscoveryPlan:
    """Find data center permits by site name and pick the documents to fetch for each."""
    found = ro.permits_by_name(keywords, since=since)
    skipped = [(p, n) for p, n in found.items() if n and CONSTRUCTION_SITE_RE.search(n)]
    keep = [(p, n) for p, n in found.items() if (p, n) not in skipped]
    if max_permits:
        keep = keep[:max_permits]
    permits = [PermitPlan(p, n, choose_documents(ro.permit_documents(p))) for p, n in keep]
    return DiscoveryPlan(permits, skipped)


def choose_documents(rows: Iterable[RecordRow]) -> list[RecordRow]:
    """The technical review and final action from the most recent project that has a review.

    Later projects without a review (ownership changes, renewals of paperwork) rarely list
    equipment, so they are skipped. With no review anywhere, the newest final action is used.
    """
    by_project: dict[str | None, list[RecordRow]] = defaultdict(list)
    for r in rows:
        if r.title in WANTED_TYPES:
            by_project[r.secondary_id].append(r)
    if not by_project:
        return []

    def project_date(item: tuple[str | None, list[RecordRow]]) -> tuple:
        project, docs = item
        latest = max((d.begin_date or date.min for d in docs), default=date.min)
        return (latest, project or "")

    ordered = sorted(by_project.items(), key=project_date, reverse=True)
    for _project, docs in ordered:
        if any(d.title == "Technical Review" for d in docs):
            chosen = []
            for wanted in WANTED_TYPES:
                of_type = [d for d in docs if d.title == wanted]
                if of_type:
                    chosen.append(max(of_type, key=lambda d: (d.begin_date or date.min, d.d_id)))
            return chosen
    newest = max(
        (d for _p, docs in ordered for d in docs if d.title == "Final Action"),
        key=lambda d: (d.begin_date or date.min, d.d_id),
        default=None,
    )
    return [newest] if newest else []
