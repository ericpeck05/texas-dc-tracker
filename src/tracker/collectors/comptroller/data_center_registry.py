"""Texas Comptroller registry of data centers certified for the state sales tax exemption.

https://comptroller.texas.gov/taxes/data-centers/data-center-lists.php has two HTML tables:
"Registered Qualifying Data Center Projects" (registration IDs start with DC) and
"Registered Qualifying Large Data Center Projects" (LD). Each row names the data center
and its owner, occupant, and operator, each with a registration number such as
DC832828-OW1. A cell can hold several parties as a list.

The page gives no address or county, so matching an entry to a site found elsewhere goes
through names (see tracker.registry_import).
"""

from __future__ import annotations

import html as html_lib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

REGISTRY_URL = "https://comptroller.texas.gov/taxes/data-centers/data-center-lists.php"
SOURCE = "comptroller_dc_registry"

COLUMNS = (
    "Data Center",
    "Effective Date",
    "Owner Name",
    "Owner Registration Number",
    "Occupant Name",
    "Occupant Registration Number",
    "Operator Name",
    "Operator Registration Number",
    "Exemption End Date",
)
ROLES = (("owner", 2, 3), ("occupant", 4, 5), ("operator", 6, 7))
REG_ID_RE = re.compile(r"\b((?:DC|LD)\d{6})-(?:OW|OC|OP)\d+\b")


class RegistryFormatError(Exception):
    """The page no longer has the expected tables or columns."""


@dataclass(frozen=True)
class Party:
    role: str  # owner, occupant, operator
    name: str
    registration_number: str | None


@dataclass
class RegistryEntry:
    registration_id: str  # e.g. LD543087, shared by all parties of one data center
    program: str  # "qualifying" (DC...) or "large" (LD...)
    name: str
    effective_date: date | None
    end_date: date | None
    parties: list[Party] = field(default_factory=list)
    # Other ID prefixes in the row; usually a typo on the page (a party's number with a
    # different ID than the rest). Kept so the row can be checked by hand.
    other_ids: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        """Stable dict for change detection between pulls."""
        return {
            "registration_id": self.registration_id,
            "program": self.program,
            "name": self.name,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "parties": [[p.role, p.name, p.registration_number] for p in self.parties],
            "other_ids": self.other_ids,
        }


def _items(cell: str) -> list[str]:
    parts = re.split(r"<li[^>]*>|<br\s*/?>", cell)
    out = []
    for part in parts:
        text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", part))).strip()
        if text:
            out.append(text)
    return out


def _date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def parse_registry(page: str) -> list[RegistryEntry]:
    tables = re.findall(r"<table.*?</table>", page, re.S)
    if not tables:
        raise RegistryFormatError("no tables on page")
    entries: list[RegistryEntry] = []
    for table in tables:
        headers = [
            re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", h))).strip()
            for h in re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table, re.S)
        ]
        if tuple(headers) != COLUMNS:
            raise RegistryFormatError(f"unexpected columns: {headers}")
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
            if len(cells) != len(COLUMNS) or 'scope="col"' in row:
                continue
            name = " ".join(_items(cells[0]))
            parties: list[Party] = []
            ids: list[tuple[str, str]] = []  # (role, id prefix)
            for role, name_col, reg_col in ROLES:
                names, regs = _items(cells[name_col]), _items(cells[reg_col])
                for i, party_name in enumerate(names):
                    reg = regs[i] if i < len(regs) else None
                    parties.append(Party(role, party_name, reg))
                for reg in regs:
                    if m := REG_ID_RE.search(reg):
                        ids.append((role, m.group(1)))
            if not name or not ids:
                raise RegistryFormatError(f"row {name!r} has no registration number")
            # Key by the owner's ID, else the most common one.
            owner_ids = [i for r, i in ids if r == "owner"]
            reg_id = owner_ids[0] if owner_ids else Counter(i for _, i in ids).most_common(1)[0][0]
            entries.append(
                RegistryEntry(
                    registration_id=reg_id,
                    program="large" if reg_id.startswith("LD") else "qualifying",
                    name=name,
                    effective_date=_date(" ".join(_items(cells[1]))),
                    end_date=_date(" ".join(_items(cells[8]))),
                    parties=parties,
                    other_ids=sorted({i for _, i in ids} - {reg_id}),
                )
            )
    return entries
