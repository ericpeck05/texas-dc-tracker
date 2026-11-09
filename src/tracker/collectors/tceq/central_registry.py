"""TCEQ Central Registry files on data.texas.gov (official open data, updated daily).

The Central Registry is where TCEQ keeps every regulated entity (a site, keyed by its RN
number) and every program authorization at it: construction and industrial stormwater
(TXR15..., TXR05...), air permits (NSR numbers), fuel tanks, septic systems, and so on.
TCEQ publishes it as five regional datasets, one row per (site, program ID).

For the tracker this ties things together: the RN joins air permits, stormwater, and the
site's address and county, and the principal is the company on the TCEQ customer record.
A construction stormwater authorization is early notice that dirt is moving.

Personal data: rows whose principal is an individual are dropped before anything is saved.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from urllib.parse import urlencode

from tracker.entities import is_organization

SOURCE = "tceq_central_registry"
DATASETS = {
    "Central Texas": "msah-s2rv",
    "North Texas": "5eqq-7nad",
    "Dallas / Fort Worth": "t34q-qzi3",
    "Coastal & East Texas": "tzyg-j7q4",
    "Border & Permian Basin": "9iad-hrn8",
}
RESOURCE_URL = "https://data.texas.gov/resource/{dataset}.json"
PAGE_URL = "https://data.texas.gov/browse?q=Central%20Registry%20Files"
ROW_LIMIT = 50000
RN_CHUNK = 40

DATA_CENTER_WHERE = (
    "starts_with(indus_type_cd_name, '5182') "
    "OR upper(reg_ent_name) like '%DATA CENTER%' OR upper(reg_ent_name) like '%DATACENTER%' "
    "OR upper(reg_ent_desc_txt) like '%DATA CENTER%' OR upper(princ_name) like '%DATA CENTER%'"
)
DATA_CENTER_RE = re.compile(r"\bdata\s?cent(?:er|re)s?\b|\bdatacent", re.IGNORECASE)
# Concrete batch plants and crushers built to supply a data center job carry its name but
# are not the data center.
SUPPLY_PLANT_RE = re.compile(
    r"\b(?:CBP|batch|concrete|ready\s?mix|crush(?:er|ing)?|asphalt|quarry)\b", re.IGNORECASE
)
# Work for a data center that is not the data center: substations, conduits, laydown yards.
SUPPORT_WORK_RE = re.compile(
    r"\bsub\s?station|switch(?:ing)?\s+station|\bconduit\b|\bwwtp\b|laydown|offsites?\b",
    re.IGNORECASE,
)
# Principals that build or serve a site rather than own it: general contractors, trades,
# and utilities. They are not tied to the site as owners or used to match it.
CONTRACTOR_RE = re.compile(
    r"\b(?:construct(?:ion|ors?)|contractors?|builders?|building\s+co|electric(?:al)?,?\s+inc|"
    r"structure\s+tone|excavat\w*|grading|ready\s?mix|paving|mechanical|plumbing|"
    r"public\s+service|energy\s+delivery|oncor|centerpoint|electric\s+coop\w*|"
    r"engineering|dpr\b|turner\b|holder\b|hensel\s+phelps|rosendin|mortenson|skanska)\b",
    re.IGNORECASE,
)
INDIVIDUAL = "INDIVIDUAL OWNER TYPE"
SENTINEL_YEARS = {"1800", "3000"}


def query_url(dataset: str, where: str) -> str:
    params = {"$where": where, "$limit": str(ROW_LIMIT), "$order": "ref_num_txt"}
    return f"{RESOURCE_URL.format(dataset=dataset)}?{urlencode(params)}"


def data_center_urls() -> list[str]:
    return [query_url(ds, DATA_CENTER_WHERE) for ds in DATASETS.values()]


def rn_urls(rns: list[str]) -> list[str]:
    """Queries for sites already tracked by RN, whatever their industry code says."""
    rns = sorted({rn for rn in rns if re.fullmatch(r"RN\d{9}", rn)})
    urls = []
    for i in range(0, len(rns), RN_CHUNK):
        chunk = ", ".join(f"'{rn}'" for rn in rns[i : i + RN_CHUNK])
        urls += [query_url(ds, f"ref_num_txt in ({chunk})") for ds in DATASETS.values()]
    return urls


def _date(value: str | None) -> date | None:
    if not value or value[:4] in SENTINEL_YEARS:
        return None
    try:
        return datetime.fromisoformat(value[:19]).date()
    except ValueError:
        return None


@dataclass(frozen=True)
class ProgramId:
    program: str  # STORM, AIRNSR, PSTREG, ...
    number: str  # TXR15xxxx, 177263, ...
    status: str | None
    since: date | None

    @property
    def is_construction_stormwater(self) -> bool:
        return self.program == "STORM" and self.number.startswith("TXR15")


@dataclass
class RegulatedEntity:
    rn: str
    name: str
    description: str | None
    county: str | None
    city: str | None
    address: str | None
    industry: str | None
    principal: str | None  # organization on the customer record, unless a contractor
    contractor: str | None  # principal that builds rather than owns (not linked)
    cn: str | None
    status: str | None
    dataset: str | None = None  # data.texas.gov dataset the row came from
    programs: list[ProgramId] = field(default_factory=list)

    def as_row(self) -> dict:
        row = asdict(self)
        row["programs"] = [
            {**asdict(p), "since": p.since.isoformat() if p.since else None} for p in self.programs
        ]
        return row


def drop_individuals(rows: list[dict]) -> list[dict]:
    return [r for r in rows if (r.get("name_txt") or "").upper() != INDIVIDUAL]


def _title(text: str | None) -> str | None:
    if not text:
        return None
    return text.strip().title() if text.isupper() else text.strip()


def parse_rows(rows: list[dict]) -> list[RegulatedEntity]:
    by_rn: dict[str, RegulatedEntity] = {}
    seen: set[tuple[str, str, str]] = set()
    for r in drop_individuals(rows):
        rn = r.get("ref_num_txt")
        if not rn:
            continue
        ent = by_rn.get(rn)
        if ent is None:
            principal = r.get("princ_legal_name") or r.get("princ_name")
            if principal and not is_organization(principal):
                principal = None
            contractor = bool(principal and CONTRACTOR_RE.search(principal))
            county = _title(r.get("re_phys_loc_addr_county"))
            ent = RegulatedEntity(
                rn=rn,
                name=_title(r.get("reg_ent_name")) or rn,
                description=r.get("reg_ent_desc_txt"),
                county=county,
                city=_title(r.get("re_phys_loc_city")),
                address=r.get("re_phys_loc_addr_line_1"),
                industry=r.get("indus_type_cd_name"),
                principal=None if contractor else principal,
                contractor=principal if contractor else None,
                cn=r.get("ref_num_txt_1"),
                status=r.get("reg_ent_status_txt"),
                dataset=r.get("_dataset"),
            )
            by_rn[rn] = ent
        program, number = r.get("program_code"), r.get("additional_id_text")
        if program and number and (rn, program, number) not in seen:
            seen.add((rn, program, number))
            since = _date(r.get("affil_begin_dt")) or _date(r.get("status_dt"))
            ent.programs.append(ProgramId(program, number, r.get("additional_id_status"), since))
    for ent in by_rn.values():
        ent.programs.sort(key=lambda p: (p.program, p.number))
    return sorted(by_rn.values(), key=lambda e: e.rn)


def data_center_reason(ent: RegulatedEntity, known_company) -> str | None:
    """Why this TCEQ site is a data center, or None.

    `known_company(name)` names the company behind a principal, if one is known. A data
    hosting industry code alone also covers corporate server rooms and print shops, so it
    counts only with a known company as principal."""
    text = " ".join(filter(None, [ent.name, ent.description]))
    if SUPPLY_PLANT_RE.search(text) or SUPPORT_WORK_RE.search(text):
        return None
    if DATA_CENTER_RE.search(text) or DATA_CENTER_RE.search(ent.principal or ""):
        return "named as a data center"
    company = known_company(ent.principal) if ent.principal else None
    if (ent.industry or "").startswith("5182") and company:
        return f"industry {ent.industry}, principal belongs to {company}"
    return None


@dataclass
class Snapshot:
    """Every query of one pull and the rows it returned (individuals already dropped)."""

    urls: list[str]
    rows: list[dict]

    def to_bytes(self) -> bytes:
        return json.dumps({"urls": self.urls, "rows": self.rows}, sort_keys=True).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> Snapshot:
        data = json.loads(content)
        return cls(data["urls"], data["rows"])
