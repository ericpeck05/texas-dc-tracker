"""Texas PUC Interchange: dockets where a data center deals with the grid.

ERCOT does not publish its large-load queue by project, and its report downloads are closed
to automated clients by robots.txt. The PUC's filing system is open: data center developers
file complaints against ERCOT over their load studies, utilities ask for data center rate
riders, and new retail providers serving one campus apply for authority. Each such docket is
a grid signal.

Only the PUC's own "Export to Excel" downloads are used: one for each search (a list of
dockets) and one per docket (its filings, with file dates and filing parties).

Searches: filing and case descriptions mentioning data centers or large loads since
SINCE, plus filings by the data center companies we track. Kept: project dockets
(applications, complaints, appeals, petitions), not rulemakings, projects, reports, or rate
cases, and only when the docket, its filings, or its party point to a data center.

People: a docket brought by an individual is dropped, and filing parties that are not
organizations are removed before anything is stored.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

from tracker.entities import is_organization
from tracker.reference.companies import find_company

SOURCE = "puc_interchange"
BASE = "https://interchange.puc.texas.gov"
PAGE_URL = f"{BASE}/search/filings/"
SINCE = "01/01/2023"
KEYWORDS = ("data center", "datacenter", "data centers", "large load")
# Data center companies we track, as they appear in filing party names.
PARTY_TERMS = (
    "Amazon",
    "Google",
    "Microsoft",
    "Meta Platforms",
    "Oracle",
    "CoreWeave",
    "Core Scientific",
    "Riot",
    "Cipher",
    "Applied Digital",
    "Galaxy",
    "Digital Realty",
    "Equinix",
    "NTT",
    "Vantage",
    "QTS",
    "CyrusOne",
    "Compass",
    "Stream Data",
    "Sabey",
    "DataBank",
    "Lancium",
    "Crusoe",
    "Nexus Data",
    "Poolside",
    "Skybox",
    "Aligned",
)
DC_ROLES = {"hyperscaler", "neocloud", "developer", "reit"}

DATA_CENTER_RE = re.compile(r"\bdata\s*cent(?:er|re)s?\b|\bdatacent(?:er|re)s?\b", re.I)
DC_PARTY_RE = re.compile(r"\bDC\b|\bdata\s*cent|\bdatacent|\bdigital\s+infrastructure\b", re.I)
NOT_PROJECT_RE = re.compile(
    r"\b(?:RULEMAKING|PROJECT\s+(?:TO|NO)|REVIEW\s+OF|REPORTS?\s+OF|IMPLEMENTATION|"
    r"ASSESSMENT|DESIGNATION|DATA\s+COLLECTION|EMERGENCY\s+OPERATIONS|PROTOCOLS|"
    r"CHANGE\s+RATES|SYSTEM\s+IMPROVEMENT|PUBLIC\s+INPUT|TARIFF|DEMAND\s+MANAGEMENT|"
    r"LOAN\s+PROGRAM|MEASUREMENT\s+STANDARD)\b",
    re.I,
)
# Company-wide licenses (retail provider, QSE, operating authority) are not a site, unless
# the case itself names a data center.
LICENSE_RE = re.compile(
    r"\b(?:RETAIL\s+ELECTRIC\s+PROVIDER|QSE|QUALIFIED\s+SCHEDULING|OPERATING\s+AUTHORITY)\b", re.I
)
PROJECT_RE = re.compile(r"\b(?:APPLICATION|COMPLAINT|APPEAL|PETITION|REQUEST)\b", re.I)
UTILITY_RE = re.compile(
    r"\b(?:ELECTRIC\s+(?:COMPANY|DELIVERY|COOPERATIVE|COOP)|POWER\s+COMPANY|"
    r"PUBLIC\s+SERVICE|TRANSMISSION|ENERGY\s+DELIVERY|CENTERPOINT|ONCOR|AEP\b|"
    r"ELECTRIC\s+RELIABILITY\s+COUNCIL)",
    re.I,
)
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def search_url(**params: str) -> str:
    query = {
        "UtilityType": "A",
        "ItemMatch": "Equal",
        "DateFiledFrom": SINCE,
        "SortBy": "ControlNumber",
        "SortOrder": "Ascending",
        **params,
    }
    return f"{BASE}/search/exportdockets/?{urlencode(query)}"


def search_urls() -> list[str]:
    urls = []
    for kw in KEYWORDS:
        urls.append(search_url(FilingDescription=kw))
        urls.append(search_url(Description=kw))
    urls += [search_url(FilingParty=term) for term in PARTY_TERMS]
    return urls


def filings_url(control: str) -> str:
    query = {
        "UtilityType": "A",
        "ControlNumber": control,
        "ItemMatch": "Equal",
        "SortBy": "ItemNumber",
        "SortOrder": "Ascending",
    }
    return f"{BASE}/search/exportfilings/?{urlencode(query)}"


def docket_url(control: str) -> str:
    return f"{BASE}/search/filings/?ControlNumber={control}"


def read_xlsx(content: bytes) -> list[dict[str, str | None]]:
    """Rows of the first sheet as dicts keyed by the header row (no spreadsheet library)."""
    z = zipfile.ZipFile(io.BytesIO(content))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    rows: list[list[str | None]] = []
    for r in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(f"{_NS}row"):
        vals: list[str | None] = []
        for c in r.iter(f"{_NS}c"):
            v, kind = c.find(f"{_NS}v"), c.get("t")
            if kind == "s" and v is not None:
                vals.append(shared[int(v.text)])
            elif kind == "inlineStr":
                vals.append("".join(t.text or "" for t in c.iter(f"{_NS}t")))
            else:
                vals.append(v.text if v is not None else None)
        rows.append(vals)
    if not rows:
        return []
    header = [(h or "").strip() for h in rows[0]]
    return [
        dict(zip(header, (" ".join((v or "").split()) or None for v in r), strict=False))
        for r in rows[1:]
    ]


def excel_date(value: str | None) -> date | None:
    try:
        return date(1899, 12, 30) + timedelta(days=int(float(value)))
    except (TypeError, ValueError):
        return None


def is_project(party: str, description: str) -> bool:
    if party.upper().startswith("PUC ") or NOT_PROJECT_RE.search(description):
        return False
    return bool(PROJECT_RE.search(description))


def is_utility(party: str) -> bool:
    return bool(UTILITY_RE.search(party))


def known_dc_company(party: str) -> str | None:
    found = find_company(party)
    if found and str(found[0].role) in DC_ROLES:
        return found[0].name
    return None


def data_center_reason(party: str, description: str, filings: list[dict]) -> str | None:
    if DATA_CENTER_RE.search(description):
        return "case names a data center"
    if LICENSE_RE.search(description):
        return None
    if not is_utility(party) and DC_PARTY_RE.search(party):
        return "party is a data center company"
    if company := known_dc_company(party):
        return f"party belongs to {company}"
    if any(DATA_CENTER_RE.search(f.get("description") or "") for f in filings):
        return "a filing names a data center"
    return None


@dataclass
class Docket:
    control: str
    party: str
    description: str
    filings: list[dict] = field(default_factory=list)  # item, date, party, description
    reason: str | None = None

    @property
    def url(self) -> str:
        return docket_url(self.control)

    @property
    def first_filed(self) -> date | None:
        dates = [date.fromisoformat(f["date"]) for f in self.filings if f.get("date")]
        return min(dates) if dates else None

    @property
    def last_filed(self) -> date | None:
        dates = [date.fromisoformat(f["date"]) for f in self.filings if f.get("date")]
        return max(dates) if dates else None

    def as_dict(self) -> dict:
        return {
            "control": self.control,
            "party": self.party,
            "description": self.description,
            "filings": self.filings,
            "reason": self.reason,
        }


def parse_filings(rows: list[dict]) -> list[dict]:
    """Filing rows with organization parties only (a person's name is never kept)."""
    out = []
    for r in rows:
        party = r.get("Filing Party") or ""
        filed = excel_date(r.get("File Stamp"))
        out.append(
            {
                "item": r.get("Item #"),
                "date": filed.isoformat() if filed else None,
                "party": party if party and is_organization(party) else None,
                "description": r.get("Filing Description") if is_organization(party) else None,
            }
        )
    return out


@dataclass
class Snapshot:
    urls: list[str]
    dockets: list[dict]

    def to_bytes(self) -> bytes:
        return json.dumps({"urls": self.urls, "dockets": self.dockets}, sort_keys=True).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> Snapshot:
        data = json.loads(content)
        return cls(data["urls"], data["dockets"])


def dockets_from(snapshot: Snapshot) -> list[Docket]:
    return [Docket(**d) for d in snapshot.dockets]


def fetch(client) -> Snapshot:
    """Run every search, keep project dockets with a data center tie, and read their filings."""
    candidates: dict[str, tuple[str, str]] = {}
    urls = search_urls()
    for url in urls:
        for row in read_xlsx(client.get(url).content):
            control = row.get("Control #")
            party = row.get("Utility") or ""
            description = row.get("Description (Case Style)") or ""
            if not control or not is_organization(party) or not is_project(party, description):
                continue
            candidates[control] = (party, description)
    dockets = []
    for control, (party, description) in sorted(candidates.items(), key=lambda kv: int(kv[0])):
        url = filings_url(control)
        urls.append(url)
        filings = parse_filings(read_xlsx(client.get(url).content))
        reason = data_center_reason(party, description, filings)
        if reason:
            dockets.append(Docket(control, party, description, filings, reason).as_dict())
    return Snapshot(urls, dockets)
