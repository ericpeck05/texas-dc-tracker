"""Comptroller Chapter 312 property tax abatement agreements (official CSV download).

Cities and counties report every new, modified, or cancelled Chapter 312 abatement to the
Comptroller, which publishes them at
https://comptroller.texas.gov/economy/development/search-tools/ch312/abatements-simple.php
with a CSV download. The detail file has one row per taxing unit, so one agreement
(Record ID) spans several rows; they are grouped here.

An abatement is a local approval: a city or county voted to cut a project's property tax,
and the file says where (appraisal district, taxing units) and who owns the property.

Personal data: some agreements are with individuals (home and small-business abatements
in enterprise zones), and every row names the local official who filed it. Only
organization owners are kept, and the filer's name is never read.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from tracker.db.models import CompanyRole
from tracker.entities import entity_key, is_organization
from tracker.reference.companies import find_company

DETAIL_URL = "https://assets.comptroller.texas.gov/open-data-files/ch312-abatement-detail.csv"
PAGE_URL = (
    "https://comptroller.texas.gov/economy/development/search-tools/ch312/abatements-simple.php"
)
AGREEMENT_URL = (
    "https://comptroller.texas.gov/economy/development/search-tools/ch312/"
    "abatements-details.php?id={record_id}"
)
SOURCE = "comptroller_ch312"

REQUIRED = (
    "Record ID",
    "New Record ID Linked to Older Record ID",
    "CAD Name (Reporting Entity)",
    "Lead Taxing Unit Name",
    "Reinvestment Zone Name",
    "Abatement Status",
    "Taxing Unit Name",
    "Property Owner Name",
    "NAICS Code",
    "Executed/Signed Date of Agreement",
)
DATA_CENTER_NAICS = "5182"  # 518210: computing infrastructure, data processing, hosting
DATA_CENTER_RE = re.compile(r"\bdata\s?cent(?:er|re)s?\b|\bdatacent", re.IGNORECASE)
COMPUTE_RE = re.compile(r"\bsuper[\s-]?comput\w*|\bcompute\s+(?:facility|campus|center)\b", re.I)
# Owner cells list several owners: "ET Amarillo LLC & Amazon.com Services LLC".
OWNER_SPLIT_RE = re.compile(r"\s+&\s+|\s*;\s*|\s+and\s+(?=[A-Z])")
GOVERNMENT_RE = re.compile(r"^(?:City|County|Town|Village|Port)\s+of\b|\bISD\b", re.IGNORECASE)
COUNTY_UNIT_RE = re.compile(r"^(.+?)\s+County$", re.IGNORECASE)


class AbatementFormatError(Exception):
    """The CSV no longer has the expected columns."""


@dataclass
class Abatement:
    record_id: str
    previous_record_id: str | None
    appraisal_district: str
    county: str | None
    lead_unit: str
    zone: str
    status: str
    owners: list[str]  # organizations only
    taxing_units: list[str]
    naics: list[str]
    executed: date | None
    effective: date | None
    expires: date | None
    base_value: int | None
    people_omitted: int = 0  # owners dropped because they look like individuals
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return AGREEMENT_URL.format(record_id=self.record_id)

    def as_row(self) -> dict:
        row = asdict(self)
        for key in ("executed", "effective", "expires"):
            row[key] = row[key].isoformat() if row[key] else None
        row.pop("extra")
        return row

    def place_words(self) -> set[str]:
        """Words naming where the agreement is: 'Houston City' -> houston."""
        words: set[str] = set()
        for name in (self.county or "", self.lead_unit, *self.taxing_units):
            name = re.sub(r"\b(?:city|county|of|town|isd)\b", " ", name.lower())
            words |= {w for w in re.findall(r"[a-z]+", name) if len(w) > 2}
        return words


def _date(value: str) -> date | None:
    value = (value or "").strip()
    if not value or value.startswith("01/01/1900"):
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _int(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None


def split_owners(cell: str) -> list[str]:
    return [o.strip(" ,") for o in OWNER_SPLIT_RE.split(cell or "") if o.strip(" ,")]


def _county(district: str, units: list[str]) -> str | None:
    """County from the appraisal district ('Fort Bend CAD'), checked against county taxing
    units when the district covers two counties ('Potter Randall CAD')."""
    base = re.sub(r"\s+(?:CAD|Appraisal District)$", "", district.strip(), flags=re.I).strip()
    counties = [m.group(1) for u in units if (m := COUNTY_UNIT_RE.match(u.strip()))]
    for c in counties:
        if c.lower() == base.lower():
            return c
    in_district = [c for c in counties if c.lower() in base.lower()]
    if len(in_district) == 1:
        return in_district[0]
    return base or None


def parse_detail(content: bytes) -> list[Abatement]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = {(h or "").strip() for h in reader.fieldnames or []}
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise AbatementFormatError(f"missing columns: {', '.join(missing)}")
    grouped: dict[str, list[dict]] = {}
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if row["Record ID"]:
            grouped.setdefault(row["Record ID"], []).append(row)

    out = []
    for record_id, rows in grouped.items():
        first = rows[0]

        def values(col: str, rows=rows) -> list[str]:
            return sorted({r.get(col, "") for r in rows} - {""})

        owners, people = [], 0
        for cell in values("Property Owner Name"):
            for name in split_owners(cell):
                if GOVERNMENT_RE.search(name):
                    continue
                if is_organization(name):
                    if all(entity_key(name) != entity_key(o) for o in owners):
                        owners.append(name)
                else:
                    people += 1
        units = values("Taxing Unit Name")
        district = first["CAD Name (Reporting Entity)"]
        out.append(
            Abatement(
                record_id=record_id,
                previous_record_id=first["New Record ID Linked to Older Record ID"] or None,
                appraisal_district=district,
                county=_county(district, [first["Lead Taxing Unit Name"], *units]),
                lead_unit=first["Lead Taxing Unit Name"],
                zone=first["Reinvestment Zone Name"],
                status=first["Abatement Status"],
                owners=owners,
                taxing_units=units,
                naics=values("NAICS Code"),
                executed=_date(first["Executed/Signed Date of Agreement"]),
                effective=_date(first.get("Effective Date of Agreement", "")),
                expires=_date(first.get("Expiration Date of Agreement", "")),
                base_value=_int(first.get("Base Value of Abated Property", "")),
                people_omitted=people,
            )
        )
    return out


# Companies whose Texas property is data centers (not warehouses or offices), so an
# abatement in their name is a data center even without a NAICS code.
DC_ROLES = {CompanyRole.DEVELOPER, CompanyRole.NEOCLOUD}
DC_REITS = {"Equinix", "Digital Realty"}


def dc_company(name: str) -> str | None:
    """The data-center-only company behind an owner name, if any."""
    found = find_company(name)
    if not found:
        return None
    known = found[0]
    return known.name if known.role in DC_ROLES or known.name in DC_REITS else None


def data_center_reason(ab: Abatement, known_entities: set[str]) -> str | None:
    """Why this agreement is about a data center, or None.

    `known_entities` holds entity keys already tied to a tracked site. An owner that
    belongs to a company with other kinds of property (Amazon warehouses) doesn't count
    through that rule."""
    if not ab.owners:
        return None
    if any(code.startswith(DATA_CENTER_NAICS) for code in ab.naics):
        return f"NAICS {', '.join(ab.naics)} (data hosting)"
    if DATA_CENTER_RE.search(ab.zone) or any(DATA_CENTER_RE.search(o) for o in ab.owners):
        return "named as a data center"
    if COMPUTE_RE.search(ab.zone) or any(COMPUTE_RE.search(o) for o in ab.owners):
        return "named as a compute facility"
    for owner in ab.owners:
        if find_company(owner) and not dc_company(owner):
            continue
        if entity_key(owner) in known_entities:
            return f"owner '{owner}' is already tied to a tracked site"
    for owner in ab.owners:
        if company := dc_company(owner):
            return f"owner '{owner}' belongs to {company}"
    return None
