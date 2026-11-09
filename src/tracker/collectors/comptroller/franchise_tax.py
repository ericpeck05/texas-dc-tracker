"""Comptroller "Active Franchise Taxpayers" on the Texas Open Data Portal.

https://data.texas.gov/dataset/Active-Franchise-Taxpayers/9cir-efmm is the official
download behind the Comptroller's taxable entity search: every active franchise taxpayer
with its mailing address, Secretary of State file number, and SOS charter (formation) date.
It has no officers or owners. It is queried through the portal's SODA API (robots.txt
allows it), one name at a time.

Names are upper case and cut at 50 characters ("SF HOUH, LLC").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from tracker.entities import entity_key

DATASET_URL = "https://data.texas.gov/resource/9cir-efmm.json"
PAGE_URL = "https://data.texas.gov/dataset/Active-Franchise-Taxpayers/9cir-efmm"
SOURCE = "comptroller_franchise_tax"
NAME_LIMIT = 50  # the dataset cuts names at this length
ATTN_RE = re.compile(r"\s*\b(?:ATTN\b:?|C/O\b).*$", re.IGNORECASE)


@dataclass(frozen=True)
class Taxpayer:
    taxpayer_number: str
    name: str
    address: str
    city: str
    state: str
    zip: str
    sos_file_number: str | None
    charter_date: date | None
    org_type: str | None

    @property
    def full_address(self) -> str:
        """Mailing address without "ATTN:" or "C/O" lines, which can name a person."""
        street = ATTN_RE.sub("", self.address).strip(" ,")
        return f"{street}, {self.city}, {self.state} {self.zip}"

    @property
    def address_key(self) -> tuple[str, str]:
        return (self.address, self.zip)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def parse_rows(rows: list[dict]) -> list[Taxpayer]:
    return [
        Taxpayer(
            taxpayer_number=r["taxpayer_number"],
            name=r["taxpayer_name"],
            address=r.get("taxpayer_address", ""),
            city=r.get("taxpayer_city", ""),
            state=r.get("taxpayer_state", ""),
            zip=r.get("taxpayer_zip", ""),
            sos_file_number=r.get("secretary_of_state_sos_or_coa_file_number"),
            charter_date=_date(r.get("sos_charter_date")),
            org_type=r.get("taxpayer_organizational_type"),
        )
        for r in rows
        if r.get("taxpayer_number") and r.get("taxpayer_name")
    ]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def name_query_url(legal_name: str, limit: int = 200) -> str:
    """Taxpayers whose name starts with the entity's first words (filtered by name_matches)."""
    words = entity_key(legal_name).upper().split()
    prefix = " ".join(words[:3])
    params = {
        "$where": f"upper(taxpayer_name) like {_quote(prefix + '%')}",
        "$order": "taxpayer_number",
        "$limit": str(limit),
    }
    return f"{DATASET_URL}?{urlencode(params)}"


def address_query_url(address: str, zip_code: str, limit: int = 60) -> str:
    """Taxpayers at one mailing address (limit+ rows means a shared agent office)."""
    params = {
        "$where": f"taxpayer_address={_quote(address)} and taxpayer_zip={_quote(zip_code)}",
        "$order": "taxpayer_number",
        "$limit": str(limit),
    }
    return f"{DATASET_URL}?{urlencode(params)}"


def name_matches(legal_name: str, taxpayer_name: str) -> bool:
    """Same entity: same normalized name, or the dataset's name is ours cut at 50 chars."""
    ours, theirs = entity_key(legal_name), entity_key(taxpayer_name)
    if ours == theirs:
        return True
    if len(taxpayer_name) < NAME_LIMIT - 1:
        return False
    cut = re.sub(r"[^a-z0-9]+", " ", taxpayer_name.lower()).strip()
    full = re.sub(r"[^a-z0-9]+", " ", legal_name.lower()).strip()
    return full.startswith(cut)


def best_matches(legal_name: str, taxpayers: list[Taxpayer]) -> list[Taxpayer]:
    """Taxpayers that are this entity. When "Lancium LLC" and "LANCIUM, INC." both match by
    base name, the one with the same legal suffix wins."""
    matches = [t for t in taxpayers if name_matches(legal_name, t.name)]
    if len(matches) > 1:

        def norm(n: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", n.lower())

        exact = [t for t in matches if norm(t.name) == norm(legal_name)]
        if exact:
            return exact
    return matches


@dataclass
class Lookup:
    """One saved lookup: the query, what it was for, and the rows that came back."""

    kind: str  # "name" or "address"
    query: str  # legal name, or "address|zip"
    url: str
    rows: list[dict]

    def to_bytes(self) -> bytes:
        body = {"kind": self.kind, "query": self.query, "url": self.url, "rows": self.rows}
        return json.dumps(body, sort_keys=True, indent=1).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> Lookup:
        d = json.loads(content)
        return cls(d["kind"], d["query"], d["url"], d["rows"])
