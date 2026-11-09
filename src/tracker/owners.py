"""Owner unmasking: who is behind the project LLCs that file for data center sites.

Three steps, each kept as evidence:

1. Franchise tax lookup (free, automatic). Each entity tied to a site that has no
   high-confidence company is looked up by name in the Comptroller's Active Franchise
   Taxpayers file. A unique match gives its mailing address, SOS file number, and formation
   date.
2. Address siblings (automatic). Other taxpayers at the same mailing address are often
   sister project LLCs ("SF HOUC" and "SF HOUH" at one El Segundo PO box). An address
   shared by more than AGENT_OFFICE_MIN taxpayers is a registered agent's office and says
   nothing, so it is skipped. Siblings become entities with AFFILIATE_OF links: medium if
   they share the first word of the name, low otherwise. A sibling with a known company
   gives that company a low-confidence link for review.
3. Hand findings (manual). What a person learns from SOSDirect (paid, looked up by hand),
   news, or filings goes in seeds/owner_findings.csv with its evidence. They are re-applied
   on every rebuild. Companies only: never put a person's name in that file.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.comptroller.franchise_tax import (
    PAGE_URL,
    SOURCE,
    Lookup,
    Taxpayer,
    best_matches,
    parse_rows,
)
from tracker.db.models import (
    CompanyRole,
    Confidence,
    Document,
    Entity,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    SignalType,
    Site,
)
from tracker.entities import (
    RANK,
    EntityBook,
    add_link,
    entity_key,
    get_company,
    is_organization,
    link_company,
)
from tracker.reference.companies import KnownCompany, find_company

AGENT_OFFICE_MIN = 25  # more taxpayers than this at one address: a registered agent office
FINDINGS_PATH = Path(__file__).resolve().parents[2] / "seeds" / "owner_findings.csv"


def site_entity_ids(session: Session) -> dict[int, set[int]]:
    """entity id -> ids of the sites it is linked to."""
    out: dict[int, set[int]] = defaultdict(set)
    for link in session.scalars(
        select(Link).where(Link.from_type == RecordType.ENTITY, Link.to_type == RecordType.SITE)
    ):
        out[link.from_id].add(link.to_id)
    return out


def best_company_confidence(session: Session, entity_id: int) -> Confidence | None:
    confs = session.scalars(
        select(Link.confidence).where(
            Link.from_type == RecordType.COMPANY,
            Link.to_type == RecordType.ENTITY,
            Link.to_id == entity_id,
        )
    ).all()
    return max(confs, key=RANK.get) if confs else None


def lookup_targets(session: Session) -> list[Entity]:
    """Entities tied to a site with no high-confidence company behind them."""
    out = []
    for entity_id in sorted(site_entity_ids(session)):
        if best_company_confidence(session, entity_id) != Confidence.HIGH:
            out.append(session.get(Entity, entity_id))
    return out


def saved_lookups(session: Session) -> dict[tuple[str, str], Lookup]:
    """(kind, query) -> the latest saved lookup. Name queries are keyed by entity_key."""
    out = {}
    for doc in session.scalars(
        select(Document).where(Document.source == SOURCE).order_by(Document.id)
    ):
        lookup = Lookup.from_bytes(Path(doc.local_path).read_bytes())
        query = entity_key(lookup.query) if lookup.kind == "name" else lookup.query
        out[(lookup.kind, query)] = lookup
    return out


def address_query(t: Taxpayer) -> str:
    return f"{t.address}|{t.zip}"


def _fill(entity: Entity, t: Taxpayer) -> None:
    entity.taxpayer_number = t.taxpayer_number
    entity.sos_file_number = t.sos_file_number
    entity.formation_date = t.charter_date
    entity.address = t.full_address


def apply_lookup(session: Session, lookup: Lookup) -> str:
    """Record a lookup's result. Returns a short status line."""
    book = EntityBook(session)
    taxpayers = parse_rows(lookup.rows)
    if lookup.kind == "name":
        entity = book.by_key.get(entity_key(lookup.query))
        if entity is None:
            return f"no entity named {lookup.query!r}"
        matches = best_matches(lookup.query, taxpayers)
        if len(matches) != 1:
            return f"{len(matches)} franchise taxpayers match {lookup.query!r}"
        _fill(entity, matches[0])
        return f"found {matches[0].name}: SOS {matches[0].sos_file_number}"
    return _apply_address(session, book, lookup, taxpayers)


def _apply_address(
    session: Session, book: EntityBook, lookup: Lookup, taxpayers: list[Taxpayer]
) -> str:
    address = taxpayers[0].full_address if taxpayers else lookup.query
    ours = session.scalars(select(Entity).where(Entity.address == address)).all()
    on_sites = site_entity_ids(session)
    ours = [e for e in ours if e.id in on_sites]
    owner = address_company(taxpayers)
    if owner is not None:
        known, n = owner
        company = get_company(session, known.name, known.role, known.ticker, known.exchange)
        for entity in ours:
            add_link(
                session,
                from_type=RecordType.COMPANY,
                from_id=company.id,
                to_type=RecordType.ENTITY,
                to_id=entity.id,
                link_type=LinkType.PARENT_OF,
                method=LinkMethod.ADDRESS_MATCH,
                confidence=Confidence.MEDIUM,
                evidence_url=PAGE_URL,
                note=f"mailing address {address} is {known.name}'s: {n} of the "
                f"{len(taxpayers)} taxpayers there carry its name and no other company's",
            )
    if len(taxpayers) > AGENT_OFFICE_MIN:
        if owner is not None:
            return f"{address}: {owner[0].name}'s address ({len(ours)} of our entities)"
        return f"{lookup.query}: {len(taxpayers)}+ taxpayers, an agent office (skipped)"
    added = 0
    for t in taxpayers:
        if not is_organization(t.name):
            continue
        sibling = book.get(t.name)
        if sibling.taxpayer_number is None:
            _fill(sibling, t)
        link_company(session, sibling, t.name)
        for entity in ours:
            if entity.id == sibling.id:
                continue
            same_family = _first_word(entity.legal_name) == _first_word(t.name)
            conf = Confidence.MEDIUM if same_family else Confidence.LOW
            added += add_link(
                session,
                from_type=RecordType.ENTITY,
                from_id=sibling.id,
                to_type=RecordType.ENTITY,
                to_id=entity.id,
                link_type=LinkType.AFFILIATE_OF,
                method=LinkMethod.ADDRESS_MATCH,
                confidence=conf,
                evidence_url=PAGE_URL,
                note=f"same franchise tax mailing address ({address}, "
                f"{len(taxpayers)} taxpayers there)",
            )
            if same_family:
                _borrow_company(session, sibling, entity, address)
    session.flush()
    return f"{address}: {len(taxpayers)} taxpayers, {added} affiliate links"


def address_company(taxpayers: list[Taxpayer]) -> tuple[KnownCompany, int] | None:
    """The known company whose address this is: at least two taxpayers there carry its name,
    and none carries another known company's (1601 Willow Rd: Meta Platforms Technologies,
    Facebook Procurement, ...). Registered agent offices mix many companies, so they fail."""
    seen: dict[str, tuple[KnownCompany, int]] = {}
    for t in taxpayers:
        found = find_company(t.name)
        if found:
            known = found[0]
            seen[known.name] = (known, seen.get(known.name, (known, 0))[1] + 1)
    if len(seen) == 1:
        known, n = next(iter(seen.values()))
        if n >= 2:
            return known, n
    return None


def _first_word(name: str) -> str:
    words = entity_key(name).split()
    return words[0] if words else ""


def _borrow_company(session: Session, sibling: Entity, entity: Entity, address: str) -> None:
    """A sister LLC's known company is a lead for this one, at low confidence."""
    for link in session.scalars(
        select(Link).where(
            Link.from_type == RecordType.COMPANY,
            Link.to_type == RecordType.ENTITY,
            Link.to_id == sibling.id,
        )
    ).all():
        add_link(
            session,
            from_type=RecordType.COMPANY,
            from_id=link.from_id,
            to_type=RecordType.ENTITY,
            to_id=entity.id,
            link_type=LinkType.PARENT_OF,
            method=LinkMethod.ADDRESS_MATCH,
            confidence=Confidence.LOW,
            evidence_url=PAGE_URL,
            note=f"sister LLC '{sibling.legal_name}' at {address} belongs to this company",
        )


@dataclass
class Finding:
    entity: str
    company: str
    ticker: str | None
    exchange: str | None
    role: CompanyRole
    confidence: Confidence
    evidence_url: str
    note: str


class FindingError(ValueError):
    pass


def read_findings(path: Path = FINDINGS_PATH) -> list[Finding]:
    if not path.exists():
        return []
    out = []
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("entity") or "").strip():
                continue
            if not is_organization(row["entity"]):
                raise FindingError(f"{path}:{i}: entity {row['entity']!r} is not a company name")
            if not row.get("evidence_url", "").startswith("http"):
                raise FindingError(f"{path}:{i}: every finding needs an evidence_url")
            out.append(
                Finding(
                    entity=row["entity"].strip(),
                    company=row["company"].strip(),
                    ticker=(row.get("ticker") or "").strip() or None,
                    exchange=(row.get("exchange") or "").strip() or None,
                    role=CompanyRole((row.get("role") or "other").strip()),
                    confidence=Confidence((row.get("confidence") or "medium").strip()),
                    evidence_url=row["evidence_url"].strip(),
                    note=(row.get("note") or "").strip(),
                )
            )
    return out


def apply_findings(session: Session, findings: list[Finding]) -> list[str]:
    """Hand-researched company links. Returns entities not found in the database."""
    book = EntityBook(session)
    missing = []
    for f in findings:
        entity = book.by_key.get(entity_key(f.entity))
        if entity is None:
            missing.append(f.entity)
            continue
        company = get_company(session, f.company, f.role, f.ticker, f.exchange)
        add_link(
            session,
            from_type=RecordType.COMPANY,
            from_id=company.id,
            to_type=RecordType.ENTITY,
            to_id=entity.id,
            link_type=LinkType.PARENT_OF,
            method=LinkMethod.MANUAL,
            confidence=f.confidence,
            evidence_url=f.evidence_url,
            note=f.note or None,
        )
    session.flush()
    return missing


@dataclass
class QueueItem:
    entity: Entity
    sites: list[Site]
    company_confidence: Confidence | None
    status: str  # not looked up, found, not found, ambiguous
    agent_office: bool = False
    has_air_permit: bool = False
    affiliates: list[tuple[str, str]] = field(default_factory=list)  # (name, confidence)

    @property
    def next_step(self) -> str:
        if self.status == "not looked up":
            return "run `tracker owners lookup`"
        if self.status == "found" and not self.entity.sos_file_number:
            return "out-of-state filer with no SOS number: check its home state's registry"
        if self.status == "found":
            return (
                f"SOSDirect: file {self.entity.sos_file_number} for managers, members, "
                "and filing history (paid, by hand); record the company in "
                "seeds/owner_findings.csv"
            )
        return "search SOSDirect by name (paid, by hand); may be registered out of state"


def owner_queue(session: Session) -> list[QueueItem]:
    lookups = saved_lookups(session)
    sites_of = site_entity_ids(session)
    items = []
    for entity in lookup_targets(session):
        lookup = lookups.get(("name", entity_key(entity.legal_name)))
        if entity.taxpayer_number:
            status = "found"
        elif lookup is None:
            status = "not looked up"
        else:
            n = len(best_matches(entity.legal_name, parse_rows(lookup.rows)))
            status = "ambiguous" if n > 1 else "not found"
        agent_office = False
        if entity.address and entity.taxpayer_number:
            for (kind, _query), lk in lookups.items():
                if kind == "address" and len(lk.rows) > AGENT_OFFICE_MIN:
                    rows = parse_rows(lk.rows)
                    if rows[0].full_address == entity.address and not address_company(rows):
                        agent_office = True
        affiliates = [
            (session.get(Entity, link.from_id).legal_name, str(link.confidence))
            for link in session.scalars(
                select(Link).where(
                    Link.link_type == LinkType.AFFILIATE_OF,
                    Link.to_type == RecordType.ENTITY,
                    Link.to_id == entity.id,
                )
            )
        ]
        sites = [session.get(Site, i) for i in sorted(sites_of[entity.id])]
        items.append(
            QueueItem(
                entity=entity,
                sites=sites,
                has_air_permit=any(
                    sig.type == SignalType.AIR_PERMIT for site in sites for sig in site.signals
                ),
                company_confidence=best_company_confidence(session, entity.id),
                status=status,
                agent_office=agent_office,
                affiliates=sorted(affiliates),
            )
        )
    # Worth paying to look up first: sites with generators on file, then the most sites.
    items.sort(key=lambda i: (not i.has_air_permit, -len(i.sites), i.entity.legal_name))
    return items
