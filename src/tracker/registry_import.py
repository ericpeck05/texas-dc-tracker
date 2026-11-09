"""Load the Comptroller data center registry: entries, parties, sites, and company links.

Each registry entry becomes an INCENTIVE signal keyed by its registration ID. Each party
(owner, occupant, operator) becomes an Entity linked to the entry's site with high
confidence, since a government filing names it in that role.

The registry gives no location, so an entry is matched to an existing site by name. In
order, stopping at the first that applies:

1. Entity match (medium): a party is the same legal entity as a permit holder or alias on
   exactly one existing site ("Abilene DC 1, LLC" is a TCEQ applicant at Longhorn).
   Names shared by several sites ("Microsoft Corporation") don't decide anything.
2. Name match (medium): the data center name has the same distinctive words as exactly
   one site's name ("Microsoft Corporation (SAT 89-90)" and "Microsoft Sat 8990 Data
   Center").
3. Phase match (medium): the name is another phase of a site already matched ("Lancium
   Abilene Clean Campus III" after "Lancium Abilene Clean Campus") and the two entries
   share a party that isn't a big tenant.

Otherwise the entry gets its own site. Any weaker candidate (a phase match with no shared
party, or several possible sites) is recorded as a low-confidence SAME_SITE link for
review instead of merging.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.comptroller.data_center_registry import (
    REGISTRY_URL,
    SOURCE,
    RegistryEntry,
)
from tracker.db.models import (
    Company,
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
    Ticker,
)
from tracker.reference.companies import find_company

EXTRACTOR = "comptroller_registry_table"
EXTRACTOR_VERSION = "0.1.0"
ROLE_LINK = {"owner": LinkType.OWNS, "occupant": LinkType.OCCUPIES, "operator": LinkType.OPERATES}

_SUFFIXES = {"llc", "l", "c", "inc", "incorporated", "corp", "corporation", "co", "company"}
_SUFFIXES |= {"lp", "p", "ltd", "pbc", "plc", "the"}
_GENERIC = {"data", "center", "centers", "datacenter", "datacenters", "campus", "project"}
_GENERIC |= {"site", "the", "and", "of", "corporation", "llc", "inc", "lp"}
_ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}


def entity_key(name: str) -> str:
    """'Abilene Dc 1, LLC' and 'Abilene DC 1 LLC' -> 'abilene dc 1'."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def name_tokens(name: str) -> frozenset[str]:
    """Distinctive words of a site name. Digit groups are joined: 'SAT 89-90' -> sat 8990."""
    text = re.sub(r"[^a-z0-9]+", " ", name.lower())
    text = re.sub(r"\b(\d+) (?=\d)", r"\1", text)
    return frozenset(w for w in text.split() if w not in _GENERIC)


def stem_tokens(name: str) -> frozenset[str]:
    """Name tokens without a trailing phase numeral: 'Clean Campus III' -> clean."""
    return frozenset(w for w in name_tokens(name) if w not in _ROMAN)


@dataclass
class Match:
    entry: RegistryEntry
    site: Site
    how: str  # entity, name, phase, new
    confidence: Confidence | None
    note: str


@dataclass
class ImportReport:
    new: list[Match] = field(default_factory=list)
    changed: list[RegistryEntry] = field(default_factory=list)
    unchanged: int = 0
    dropped: list[Signal] = field(default_factory=list)  # listed before, missing now
    review: list[str] = field(default_factory=list)  # low-confidence links added


class _SiteIndex:
    """Names of existing sites from other sources, for matching."""

    def __init__(self, session: Session) -> None:
        self.by_entity: dict[str, set[int]] = defaultdict(set)
        self.by_name: dict[frozenset[str], set[int]] = defaultdict(set)
        self.sites: dict[int, Site] = {}
        for site in session.scalars(select(Site)):
            self.add(site, include_registry=False)

    def add(self, site: Site, *, include_registry: bool = True) -> None:
        self.sites[site.id] = site
        names = {site.name, *site.aliases}
        for sig in site.signals:
            if sig.source == SOURCE and not include_registry:
                continue
            for ex in sig.extractions:
                if applicant := ex.fields.get("applicant"):
                    names.add(applicant)
        for n in names:
            if key := entity_key(n):
                self.by_entity[key].add(site.id)
            if tokens := name_tokens(n):
                self.by_name[tokens].add(site.id)


def _get_entity(session: Session, name: str, cache: dict[str, Entity]) -> Entity:
    if name in cache:
        return cache[name]
    entity = session.scalar(select(Entity).where(Entity.legal_name == name))
    if entity is None:
        m = re.search(r"\b(LLC|L\.L\.C\.|Inc\.?|Corporation|Corp\.?|L\.?P\.?|Ltd\.?|PBC)\s*$", name)
        entity = Entity(legal_name=name, entity_type=m.group(1).rstrip(".") if m else None)
        session.add(entity)
        session.flush()
    cache[name] = entity
    return entity


def _get_company(session: Session, name: str, role, ticker, exchange) -> Company:
    company = session.scalar(select(Company).where(Company.name == name))
    if company is None:
        company = Company(name=name, role=role, is_public=ticker is not None)
        session.add(company)
        session.flush()
        if ticker:
            session.add(Ticker(company_id=company.id, symbol=ticker, exchange=exchange))
    return company


def _add_link(session: Session, **kw) -> bool:
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


_LINK_KEY_FIELDS = ("from_type", "from_id", "to_type", "to_id", "link_type", "method", "confidence")


def _is_big_tenant(name: str) -> bool:
    found = find_company(name)
    return bool(found and found[0].ticker)


def _match(
    entry: RegistryEntry, index: _SiteIndex, registry_sites: dict[int, set[str]]
) -> tuple[Site | None, str, str, list[Site]]:
    """(site, how, note, weaker candidates for review)."""
    # 1. Entity match on a single site.
    for party in entry.parties:
        ids = index.by_entity.get(entity_key(party.name), set())
        if len(ids) == 1:
            site = index.sites[next(iter(ids))]
            note = f"{party.role} '{party.name}' is a permit holder or alias at this site"
            return site, "entity", note, []
    # 2. Distinctive-name match on a single site.
    ids = index.by_name.get(name_tokens(entry.name), set())
    if len(ids) == 1:
        site = index.sites[next(iter(ids))]
        return site, "name", f"name '{entry.name}' matches '{site.name}'", []
    candidates = [index.sites[i] for i in ids]
    # 3. Another phase of a site already matched this run.
    stem = stem_tokens(entry.name)
    parties = {entity_key(p.name) for p in entry.parties if not _is_big_tenant(p.name)}
    for site_id, site_parties in registry_sites.items():
        site = index.sites[site_id]
        stems = {stem_tokens(n) for n in (site.name, *site.aliases)}
        if stem and stem in stems:
            shared = parties & site_parties
            if shared:
                note = f"phase of '{site.name}', shares party '{sorted(shared)[0]}'"
                return site, "phase", note, []
            candidates.append(site)
    # Review only: one name's distinctive words are all in the other's ("Project
    # Goodnight" and "Goodnight Data Center Buildings 1-4").
    if stem:
        for tokens, ids in index.by_name.items():
            if (stem < tokens or tokens < stem) and any(
                len(w) > 3 and w.isalpha() for w in stem & tokens
            ):
                candidates.extend(index.sites[i] for i in ids)
    return None, "new", "", list({s.id: s for s in candidates}.values())


def import_registry(
    session: Session, entries: list[RegistryEntry], snapshot: Document
) -> ImportReport:
    report = ImportReport()
    index = _SiteIndex(session)
    entity_cache: dict[str, Entity] = {}
    existing = {
        s.permit_number: s for s in session.scalars(select(Signal).where(Signal.source == SOURCE))
    }
    # Parties of registry entries already on each site, for phase matching.
    registry_sites: dict[int, set[str]] = defaultdict(set)
    for sig in existing.values():
        if sig.site_id is not None:
            row = sig.extractions[-1].fields
            registry_sites[sig.site_id] |= {
                entity_key(name) for _role, name, _reg in row["parties"] if not _is_big_tenant(name)
            }

    seen: set[str] = set()
    for entry in entries:
        seen.add(entry.registration_id)
        row = entry.as_row()
        fields = {**row, "snapshot_sha256": snapshot.sha256, "url": REGISTRY_URL}
        signal = existing.get(entry.registration_id)
        if signal is not None:
            last = signal.extractions[-1].fields
            if {k: last.get(k) for k in row} == row:
                report.unchanged += 1
                continue
            report.changed.append(entry)
            session.add(
                Extraction(
                    signal=signal,
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    fields=fields,
                )
            )
            site = signal.site
            matched = session.scalar(
                select(Link.confidence).where(
                    Link.from_type == RecordType.SIGNAL,
                    Link.from_id == signal.id,
                    Link.link_type == LinkType.SAME_SITE,
                )
            )
            site_confidence = matched or Confidence.HIGH
        else:
            site, how, note, weaker = _match(entry, index, registry_sites)
            if site is None:
                site = Site(name=entry.name)
                site.set_status(
                    SiteStatus.FILED,
                    note="certified for the data center sales tax exemption "
                    f"({entry.registration_id})",
                )
                session.add(site)
                session.flush()
                index.add(site)
                confidence = None
            else:
                confidence = Confidence.MEDIUM
                if entry.name not in site.aliases and entry.name != site.name:
                    site.aliases = [*site.aliases, entry.name]
            signal = Signal(
                site=site,
                type=SignalType.INCENTIVE,
                source=SOURCE,
                title=entry.name,
                filing_date=entry.effective_date,
                permit_number=entry.registration_id,
                raw_text=json.dumps(row),
            )
            session.add(signal)
            session.add(
                Extraction(
                    signal=signal,
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    fields=fields,
                )
            )
            session.flush()
            if confidence is not None:
                _add_link(
                    session,
                    from_type=RecordType.SIGNAL,
                    from_id=signal.id,
                    to_type=RecordType.SITE,
                    to_id=site.id,
                    link_type=LinkType.SAME_SITE,
                    method=LinkMethod.NAME_MATCH,
                    confidence=confidence,
                    evidence_url=REGISTRY_URL,
                    note=note,
                )
            for other in weaker:
                if other.id == site.id:
                    continue
                if _add_link(
                    session,
                    from_type=RecordType.SITE,
                    from_id=site.id,
                    to_type=RecordType.SITE,
                    to_id=other.id,
                    link_type=LinkType.SAME_SITE,
                    method=LinkMethod.NAME_MATCH,
                    confidence=Confidence.LOW,
                    evidence_url=REGISTRY_URL,
                    note=f"'{entry.name}' may be the same project as '{other.name}'",
                ):
                    report.review.append(f"{entry.name}  ?=  {other.name}")
            report.new.append(Match(entry, site, how, confidence, note))
            site_confidence = confidence or Confidence.HIGH

        registry_sites[site.id] |= {
            entity_key(p.name) for p in entry.parties if not _is_big_tenant(p.name)
        }
        _link_parties(session, entry, site, entity_cache, site_confidence)

    report.dropped = [s for reg_id, s in existing.items() if reg_id not in seen]
    session.flush()
    return report


def _link_parties(
    session: Session,
    entry: RegistryEntry,
    site: Site,
    cache: dict[str, Entity],
    site_confidence: Confidence,
) -> None:
    """The registry names each party directly (high), but if the entry was matched to a site
    found elsewhere, the party's tie to that site is only as strong as the match."""
    for party in entry.parties:
        entity = _get_entity(session, party.name, cache)
        _add_link(
            session,
            from_type=RecordType.ENTITY,
            from_id=entity.id,
            to_type=RecordType.SITE,
            to_id=site.id,
            link_type=ROLE_LINK[party.role],
            method=LinkMethod.FILING,
            confidence=site_confidence,
            evidence_url=REGISTRY_URL,
            note=f"{party.registration_number or entry.registration_id} ({entry.name})",
        )
        found = find_company(party.name)
        if found:
            known, exact = found
            company = _get_company(session, known.name, known.role, known.ticker, known.exchange)
            _add_link(
                session,
                from_type=RecordType.COMPANY,
                from_id=company.id,
                to_type=RecordType.ENTITY,
                to_id=entity.id,
                link_type=LinkType.PARENT_OF,
                method=LinkMethod.NAME_MATCH,
                confidence=Confidence.HIGH if exact else Confidence.MEDIUM,
                note=(
                    f"'{party.name}' is a legal name {known.name} files under"
                    if exact
                    else f"'{party.name}' carries the {known.name} name"
                ),
            )


def site_companies(session: Session, site: Site) -> list[tuple[str, str, str | None, str]]:
    """(role, company, ticker, confidence) for every company tied to a site via entities.

    Confidence is the weaker of the entity-to-site and company-to-entity links."""
    rank = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}
    out = {}
    entity_links = session.scalars(
        select(Link).where(
            Link.to_type == RecordType.SITE,
            Link.to_id == site.id,
            Link.from_type == RecordType.ENTITY,
        )
    ).all()
    for el in entity_links:
        for cl in session.scalars(
            select(Link).where(
                Link.to_type == RecordType.ENTITY,
                Link.to_id == el.from_id,
                Link.from_type == RecordType.COMPANY,
            )
        ):
            company = session.get(Company, cl.from_id)
            ticker = company.tickers[0].symbol if company.tickers else None
            conf = min(el.confidence, cl.confidence, key=rank.get)
            key = (str(el.link_type), company.name)
            if key not in out or rank[conf] > rank[Confidence(out[key][3])]:
                out[key] = (str(el.link_type), company.name, ticker, str(conf))
    return sorted(out.values())
