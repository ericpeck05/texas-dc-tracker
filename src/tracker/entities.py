"""Legal entities named in filings, the links that tie them to sites, and their companies.

Every source that names a party (a TCEQ permit applicant, a Comptroller registry owner)
goes through here, so the same legal entity is one record however each filing spells it
("Abilene Dc 1, LLC" and "Abilene DC 1 LLC"), and its company link is made the same way.

Only organizations become entities. A name with no company marker (LLC, Inc., Association,
...) is skipped, so a person named as an applicant never lands in the database.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import (
    Company,
    Confidence,
    Entity,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    ReviewDecision,
    Site,
    Ticker,
)
from tracker.reference.companies import find_company

_SUFFIXES = {"llc", "l", "c", "inc", "incorporated", "corp", "corporation", "co", "company"}
_SUFFIXES |= {"lp", "p", "ltd", "pbc", "plc", "the"}

ORG_RE = re.compile(
    r"\b(?:L\.?L\.?C|Inc|Incorporated|Corp|Corporation|Company|Co|L\.?P|LLP|Ltd|PBC|PLC|"
    r"Association|Partners(?:hip)?|Holdings?|Trust|Group|Authority|District|Cooperative|"
    r"City of|County|University|Data\s?Centers?|Energy|Power|Systems|Services)\b\.?",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(r"\b(LLC|L\.L\.C\.|Inc\.?|Corporation|Corp\.?|L\.?P\.?|Ltd\.?|PBC)\s*$")

_FORM_END_RE = re.compile(
    r"\b(?:L\.?L\.?C|Inc|Incorporated|Corp|Corporation|Company|Co|L\.?P|LLP|Ltd|PBC|PLC|"
    r"Association|Trust|Partnership|Cooperative|Authority|District)\.?\s*$",
    re.IGNORECASE,
)

RANK = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}


def entity_key(name: str) -> str:
    """'Abilene Dc 1, LLC' and 'Abilene DC 1 LLC' -> 'abilene dc 1'."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def is_organization(name: str) -> bool:
    return bool(ORG_RE.search(name))


class EntityBook:
    """Finds or creates entities by entity_key, so spelling and case don't split one entity.

    A name cut short at a line break ("Abilene Data Center Campus Master") is the same
    entity as the one longer name that starts with it ("... Master Association")."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.by_key: dict[str, Entity] = {}
        for entity in session.scalars(select(Entity).order_by(Entity.id)):
            self.by_key.setdefault(entity_key(entity.legal_name), entity)

    def get(self, name: str) -> Entity:
        key = entity_key(name)
        if key in self.by_key:
            return self.by_key[key]
        if _cut_short(name, key):
            longer = {id(e): e for k, e in self.by_key.items() if k.startswith(key + " ")}
            if len(longer) == 1:
                entity = next(iter(longer.values()))
                self.by_key[key] = entity
                return entity
        elif _FORM_END_RE.search(name):
            # The full name of an entity first seen cut short.
            shorter = {
                id(e): e
                for k, e in self.by_key.items()
                if key.startswith(k + " ") and _cut_short(e.legal_name, k)
            }
            if len(shorter) == 1:
                entity = next(iter(shorter.values()))
                entity.legal_name = name
                self.by_key[key] = entity
                return entity
        m = _TYPE_RE.search(name)
        entity = Entity(legal_name=name, entity_type=m.group(1).rstrip(".") if m else None)
        self.session.add(entity)
        self.session.flush()
        self.by_key[key] = entity
        return entity


def _cut_short(name: str, key: str) -> bool:
    """A long name with no legal form at the end was likely cut at a line break."""
    return not _FORM_END_RE.search(name) and len(key.split()) >= 4


def get_company(session: Session, name: str, role, ticker, exchange) -> Company:
    company = session.scalar(select(Company).where(Company.name == name))
    if company is None:
        company = Company(name=name, role=role, is_public=ticker is not None)
        session.add(company)
        session.flush()
        if ticker:
            session.add(Ticker(company_id=company.id, symbol=ticker, exchange=exchange))
    return company


_LINK_KEY_FIELDS = ("from_type", "from_id", "to_type", "to_id", "link_type", "method", "confidence")


def not_rejected():
    """SQL condition: the link was not rejected in the review queue."""
    return Link.id.not_in(select(ReviewDecision.link_id).where(ReviewDecision.decision == "reject"))


def add_link(session: Session, **kw) -> bool:
    """Append a link unless the same one exists (links are append-only, not duplicated)."""
    exists = session.scalar(
        select(Link.id).where(
            *(getattr(Link, k) == v for k, v in kw.items() if k in _LINK_KEY_FIELDS)
        )
    )
    if exists:
        return False
    session.add(Link(**kw))
    return True


def link_company(session: Session, entity: Entity, name: str) -> None:
    """Tie an entity to the known company behind it, if any."""
    found = find_company(name)
    if not found:
        return
    known, exact = found
    company = get_company(session, known.name, known.role, known.ticker, known.exchange)
    add_link(
        session,
        from_type=RecordType.COMPANY,
        from_id=company.id,
        to_type=RecordType.ENTITY,
        to_id=entity.id,
        link_type=LinkType.PARENT_OF,
        method=LinkMethod.NAME_MATCH,
        confidence=Confidence.HIGH if exact else Confidence.MEDIUM,
        note=(
            f"'{name}' is a legal name {known.name} files under"
            if exact
            else f"'{name}' carries the {known.name} name"
        ),
    )


def link_party(
    session: Session,
    book: EntityBook,
    name: str,
    site: Site,
    link_type: LinkType,
    confidence: Confidence,
    *,
    evidence_url: str | None,
    note: str,
) -> Entity | None:
    """Entity -> site link for a party named in a filing, plus its company link."""
    if not is_organization(name):
        return None
    entity = book.get(name)
    add_link(
        session,
        from_type=RecordType.ENTITY,
        from_id=entity.id,
        to_type=RecordType.SITE,
        to_id=site.id,
        link_type=link_type,
        method=LinkMethod.FILING,
        confidence=confidence,
        evidence_url=evidence_url,
        note=note,
    )
    link_company(session, entity, name)
    return entity


def site_companies(session: Session, site: Site) -> list[tuple[str, str, str | None, str]]:
    """(role, company, ticker, confidence) for every company tied to a site via entities.

    Confidence is the weaker of the entity-to-site and company-to-entity links."""
    out: dict[tuple[str, str], tuple[str, str, str | None, str]] = {}
    entity_links = session.scalars(
        select(Link).where(
            Link.to_type == RecordType.SITE,
            Link.to_id == site.id,
            Link.from_type == RecordType.ENTITY,
            not_rejected(),
        )
    ).all()
    for el in entity_links:
        for cl in session.scalars(
            select(Link).where(
                Link.to_type == RecordType.ENTITY,
                Link.to_id == el.from_id,
                Link.from_type == RecordType.COMPANY,
                not_rejected(),
            )
        ):
            company = session.get(Company, cl.from_id)
            ticker = company.tickers[0].symbol if company.tickers else None
            conf = min(el.confidence, cl.confidence, key=RANK.get)
            key = (str(el.link_type), company.name)
            if key not in out or RANK[conf] > RANK[Confidence(out[key][3])]:
                out[key] = (str(el.link_type), company.name, ticker, str(conf))
    return sorted(out.values())


def site_entities(session: Session, site: Site) -> list[tuple[str, str, str]]:
    """(role, legal name, confidence) for every entity tied to a site, strongest link kept."""
    out: dict[tuple[str, int], tuple[str, str, str]] = {}
    for link in session.scalars(
        select(Link).where(
            Link.to_type == RecordType.SITE,
            Link.to_id == site.id,
            Link.from_type == RecordType.ENTITY,
        )
    ):
        entity = session.get(Entity, link.from_id)
        key = (str(link.link_type), entity.id)
        if key not in out or RANK[link.confidence] > RANK[Confidence(out[key][2])]:
            out[key] = (str(link.link_type), entity.legal_name, str(link.confidence))
    return sorted(out.values())
