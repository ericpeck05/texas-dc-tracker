"""PUC dockets as grid signals, matched to sites.

Each docket names applicants (the party, plus the companies in the case style). A docket
goes to a site, in order:

1. name: a distinctive word of an applicant's name ("Goodnight" in "FGE Goodnight I, LLC")
   appears in exactly one site's name.
2. party: an applicant is already an entity tied to exactly one site.
3. Several candidates (a company with many sites): no site; each candidate gets a
   low-confidence link for review.
4. None: a new site named for the applicant.

A utility's docket about an unnamed data center customer ("rate rider for a new data center
to be located in El Paso") goes to one site per utility, so its later dockets land together.

Every match from this source is medium confidence: a docket rarely names the site itself.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.puc.interchange import SOURCE, Docket, is_utility, known_dc_company
from tracker.db.models import (
    Confidence,
    Document,
    Entity,
    Extraction,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.entities import EntityBook, add_link, entity_key, is_organization, link_party
from tracker.reference.companies import find_company
from tracker.registry_import import name_tokens

EXTRACTOR = "puc_interchange_dockets"
EXTRACTOR_VERSION = "0.1.0"
MAX_CANDIDATES = 8  # more than this and a review list is noise
# Words too common in party names to identify a site.
_COMMON = {
    "energy",
    "systems",
    "infrastructure",
    "holdings",
    "power",
    "digital",
    "services",
    "partners",
    "operating",
    "operations",
    "company",
    "texas",
    "electric",
    "llc",
    "inc",
    "data",
    "center",
    "centers",
    "datacenters",
    "datacenter",
    "dc",
    "road",
    "field",
    "ranch",
    "project",
    "group",
    "solutions",
    "technologies",
    "platforms",
}
_APPLICANTS_RE = re.compile(
    r"^\s*(?:JOINT\s+)?(?:APPLICATION|COMPLAINT|PETITION|APPEAL)\s+(?:OF|FOR|BY)\s+(?P<names>.+?)"
    r"\s+(?:FOR|TO|AGAINST|REGARDING)\b",
    re.I,
)
_SHORT_WORDS = {"el", "la", "las", "los", "de", "del", "san", "and", "of", "the", "new", "fort"}
_COUNTIES = Path(__file__).resolve().parent / "reference" / "tx_counties.csv"


@dataclass
class PucMatch:
    docket: Docket
    site: Site | None
    how: str  # name, party, utility, review, new
    note: str = ""


@dataclass
class PucReport:
    dockets: int = 0
    new_signals: int = 0
    changed: int = 0
    matched: list[PucMatch] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def applicants(docket: Docket) -> list[str]:
    """Organizations the docket is brought by: the party and the names in the case style."""
    names = [p.strip() for p in re.split(r"\s+AND\s+", docket.party) if p.strip()]
    m = _APPLICANTS_RE.match(docket.description)
    if m:
        for part in re.split(r",\s*(?:AND\s+)?|\s+AND\s+", m.group("names")):
            part = part.strip(" ,")
            if part and not re.fullmatch(r"(?:LLC|L\.?L\.?C\.?|INC\.?|LP|L\.?P\.?)", part, re.I):
                names.append(part)
    # "FGE GOODNIGHT I" and "LLC" come apart on the comma: put the legal form back.
    out: list[str] = []
    for name in names:
        if out and re.fullmatch(r"(?:LLC|INC\.?|LP)", name, re.I):
            out[-1] = f"{out[-1]}, {name}"
        elif is_organization(name) or name.upper() == name:
            if entity_key(name) not in {entity_key(n) for n in out}:
                out.append(name)
    return [n for n in out if is_organization(n) or find_company(n)]


def distinctive_words(name: str) -> set[str]:
    words = set(name_tokens(name)) - _COMMON
    return {w for w in words if len(w) > 3 and not re.fullmatch(r"[ivx]+|\d+", w)}


def county_of(description: str) -> str | None:
    """The one Texas county a case style names, if exactly one."""
    with _COUNTIES.open(newline="") as f:
        counties = [r["county"] for r in csv.DictReader(f)]
    text = description.upper()
    found = {
        c
        for c in counties
        if re.search(rf"\b{re.escape(c.upper())}\s+(?:COUNTY|TEXAS|,\s*TEXAS)\b", text)
        or re.search(rf"\bLOCATED\s+IN\s+{re.escape(c.upper())}\b", text)
    }
    return found.pop() if len(found) == 1 else None


def site_name(name: str) -> str:
    base = re.sub(r",?\s+(?:LLC|L\.L\.C\.|INC\.?|LP|L\.P\.)\s*$", "", name.strip(), flags=re.I)
    words = []
    for w in base.split():
        # Keep short acronyms (ACS, DC); title-case the rest.
        acronym = len(w) <= 3 and w.isupper() and w.isalpha() and w.lower() not in _SHORT_WORDS
        words.append(w if acronym else w.capitalize())
    return " ".join(words)


class _Index:
    def __init__(self, session: Session) -> None:
        self.sites = {s.id: s for s in session.scalars(select(Site))}
        self.words: dict[int, set[str]] = {}
        for s in self.sites.values():
            ws: set[str] = set()
            for n in (s.name, *(s.aliases or [])):
                ws |= set(name_tokens(n))
            self.words[s.id] = ws
        names = {e.id: e.legal_name for e in session.scalars(select(Entity))}
        self.by_entity: dict[str, set[int]] = {}
        for link in session.scalars(
            select(Link).where(
                Link.from_type == RecordType.ENTITY,
                Link.to_type == RecordType.SITE,
                # This source's own party links would chain dockets together.
                Link.evidence_url.not_like("%interchange.puc.texas.gov%"),
            )
        ):
            self.by_entity.setdefault(entity_key(names[link.from_id]), set()).add(link.to_id)
        self.by_utility: dict[str, int] = {}
        for sig in session.scalars(select(Signal).where(Signal.source == SOURCE)):
            fields = sig.extractions[-1].fields if sig.extractions else {}
            if fields.get("utility_customer") and sig.site_id:
                self.by_utility[entity_key(fields["party"])] = sig.site_id

    def add(self, site: Site) -> None:
        self.sites[site.id] = site
        self.words[site.id] = set(name_tokens(site.name))


def _match(
    docket: Docket, names: list[str], index: _Index
) -> tuple[Site | None, str, str, list[Site]]:
    by_word: set[int] = set()
    for name in names:
        words = distinctive_words(name)
        if words:
            by_word |= {i for i, ws in index.words.items() if words & ws}
    if len(by_word) == 1:
        site = index.sites[by_word.pop()]
        return site, "name", f"a word of an applicant's name is in '{site.name}'", []
    by_party: set[int] = set()
    for name in names:
        by_party |= index.by_entity.get(entity_key(name), set())
    if len(by_party) == 1:
        site = index.sites[by_party.pop()]
        return site, "party", f"applicant is tied to '{site.name}'", []
    candidates = by_word | by_party
    if not candidates:  # a company we track, filing under a new project LLC
        companies = {c for n in names if (c := known_dc_company(n))}
        for key, ids in index.by_entity.items():
            found = find_company(key)
            if found and found[0].name in companies:
                candidates |= ids
    if candidates:
        sites = [index.sites[i] for i in sorted(candidates)]
        return None, "review", "", sites if len(sites) <= MAX_CANDIDATES else []
    return None, "new", "", []


def _extract(session: Session, signal: Signal, fields: dict) -> None:
    session.add(
        Extraction(
            signal=signal, extractor=EXTRACTOR, extractor_version=EXTRACTOR_VERSION, fields=fields
        )
    )


def import_puc(session: Session, dockets: list[Docket], snapshot: Document) -> PucReport:
    report = PucReport(dockets=len(dockets))
    book = EntityBook(session)
    index = _Index(session)
    existing = {
        s.permit_number: s for s in session.scalars(select(Signal).where(Signal.source == SOURCE))
    }
    for d in dockets:
        names = applicants(d)
        utility_customer = is_utility(d.party) and not any(not is_utility(n) for n in names)
        fields = {
            **json.loads(json.dumps(d.as_dict())),  # a copy, never the caller's lists
            "applicants": [] if utility_customer else names,
            "county": county_of(d.description),
            "utility_customer": utility_customer,
            "url": d.url,
            "snapshot": snapshot.sha256,
        }
        sig = existing.get(d.control)
        if sig is not None:
            last = sig.extractions[-1].fields if sig.extractions else {}
            if last.get("filings") != d.filings or last.get("description") != d.description:
                _extract(session, sig, fields)
                sig.filing_date = d.first_filed
                report.changed += 1
            continue

        if utility_customer:
            site_id = index.by_utility.get(entity_key(d.party))
            if site_id:
                site, how, note, review = index.sites[site_id], "utility", "same utility", []
            else:
                site, how, note, review = None, "new", "", []
        else:
            site, how, note, review = _match(d, names, index)
        created = False
        if site is None and how == "new":
            name = (
                f"{site_name(d.party)} data center customer"
                if utility_customer
                else site_name(names[0] if names else d.party)
            )
            if fields["county"]:
                name = f"{name} ({fields['county']} County)" if utility_customer else name
            site = Site(name=name, county=fields["county"])
            site.set_status(SiteStatus.FILED, note=f"PUC docket {d.control}")
            session.add(site)
            session.flush()
            index.add(site)
            created = True
        if utility_customer and site is not None:
            index.by_utility[entity_key(d.party)] = site.id

        signal = Signal(
            site=site,
            type=SignalType.GRID,
            source=SOURCE,
            title=f"PUC docket {d.control}: {d.description}",
            filing_date=d.first_filed,
            permit_number=d.control,
            raw_text=json.dumps(d.as_dict()),
        )
        session.add(signal)
        _extract(session, signal, fields)
        session.flush()
        existing[d.control] = signal
        report.new_signals += 1

        if site is not None and not created:
            add_link(
                session,
                from_type=RecordType.SIGNAL,
                from_id=signal.id,
                to_type=RecordType.SITE,
                to_id=site.id,
                link_type=LinkType.SAME_SITE,
                method=LinkMethod.NAME_MATCH,
                confidence=Confidence.MEDIUM,
                evidence_url=d.url,
                note=note,
            )
        for other in review:
            add_link(
                session,
                from_type=RecordType.SIGNAL,
                from_id=signal.id,
                to_type=RecordType.SITE,
                to_id=other.id,
                link_type=LinkType.SAME_SITE,
                method=LinkMethod.NAME_MATCH,
                confidence=Confidence.LOW,
                evidence_url=d.url,
                note=f"PUC docket {d.control} ({names[0] if names else d.party}) may concern "
                f"'{other.name}'",
            )
            report.review.append(f"{d.control} {d.party}  ?=  {other.name}")
        if site is not None and site.county is None and fields["county"]:
            site.county = fields["county"]
        if site is not None and not utility_customer:
            for name in names:
                link_party(
                    session,
                    book,
                    name,
                    site,
                    LinkType.DEVELOPS,
                    Confidence.HIGH if created else Confidence.MEDIUM,
                    evidence_url=d.url,
                    note=f"applicant in PUC docket {d.control}",
                )
        report.matched.append(PucMatch(d, site, how, note))
    return report
