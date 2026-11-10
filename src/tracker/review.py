"""Review queue: a person confirms or rejects the low-confidence links the importers leave.

Four kinds of low link reach the queue:

- same_site: two sites that may be one project. Confirming merges them: the site with fewer
  signals moves into the other (signals, equipment, links, RNs, and its name as an alias).
- filing: a filing that may belong to a site (an abatement whose owner has several sites).
  Confirming moves the filing to that site and rejects the filing's other candidates.
- parent: a company that may be behind an entity (shared mailing address).
- affiliate: two entities that may be sister LLCs (shared mailing address).

A confirm adds a Link with method=manual and high confidence. A reject hides the low link
from every view (see entities.not_rejected). Links are never deleted.

Every decision is also appended to seeds/review_decisions.csv, keyed by stable references
(an RN or site name, a filing's source and number, a legal name) rather than database ids,
so `tracker db rebuild` replays it. Notes go in that file and the public repo: companies
only, never a person's name.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from tracker.db.models import (
    Company,
    Confidence,
    Entity,
    Equipment,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    ReviewDecision,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.entities import EntityBook, entity_key, link_party, not_rejected
from tracker.views.sites import SOURCE_LABEL, _signal_row, site_row
from tracker.views.ticker import ROLE_LABEL

DECISIONS_PATH = Path(__file__).resolve().parents[2] / "seeds" / "review_decisions.csv"
KINDS = ("same_site", "filing", "parent", "affiliate")
DECISIONS = ("confirm", "reject")
PROGRESS = [
    SiteStatus.RUMORED,
    SiteStatus.FILED,
    SiteStatus.APPROVED,
    SiteStatus.UNDER_CONSTRUCTION,
    SiteStatus.OPERATING,
]


class ReviewError(ValueError):
    pass


def link_kind(link: Link) -> str | None:
    if link.link_type == LinkType.SAME_SITE and link.to_type == RecordType.SITE:
        if link.from_type == RecordType.SITE:
            return "same_site"
        if link.from_type == RecordType.SIGNAL:
            return "filing"
    if link.link_type == LinkType.PARENT_OF and link.from_type == RecordType.COMPANY:
        return "parent"
    if link.link_type == LinkType.AFFILIATE_OF and link.from_type == RecordType.ENTITY:
        return "affiliate"
    return None


# --- stable references, so decisions survive a rebuild --------------------------------------


def record_ref(session: Session, kind: RecordType, record_id: int) -> str:
    if kind == RecordType.SITE:
        site = session.get(Site, record_id)
        return f"rn:{site.tceq_rns[0]}" if site.tceq_rns else f"name:{site.name}"
    if kind == RecordType.SIGNAL:
        sig = session.get(Signal, record_id)
        url = sig.document.url if sig.source == "tceq_air" and sig.document else ""
        return "|".join(
            [sig.source, str(sig.type), sig.permit_number or "", sig.rn or "", url or ""]
        )
    if kind == RecordType.ENTITY:
        return session.get(Entity, record_id).legal_name
    if kind == RecordType.COMPANY:
        return session.get(Company, record_id).name
    raise ReviewError(f"no reference for {kind}")


def resolve_ref(session: Session, kind: RecordType, ref: str) -> int | None:
    if kind == RecordType.SITE:
        how, _, value = ref.partition(":")
        merged = set(session.scalars(select(ReviewDecision.merged_site_id)))
        for site in session.scalars(select(Site).order_by(Site.id)):
            if site.id in merged:
                continue
            if how == "rn" and value in (site.tceq_rns or []):
                return site.id
            if how == "name" and (site.name == value or value in (site.aliases or [])):
                return site.id
        return None
    if kind == RecordType.SIGNAL:
        source = ref.split("|", 1)[0]
        for sig in session.scalars(select(Signal).where(Signal.source == source)):
            if record_ref(session, kind, sig.id) == ref:
                return sig.id
        return None
    if kind == RecordType.ENTITY:
        entity = EntityBook(session).by_key.get(entity_key(ref))
        return entity.id if entity else None
    if kind == RecordType.COMPANY:
        return session.scalar(select(Company.id).where(Company.name == ref))
    return None


# --- what the queue shows ------------------------------------------------------------------


def describe(session: Session, kind: RecordType, record_id: int) -> dict:
    if kind == RecordType.SITE:
        site = session.get(Site, record_id)
        return {
            "kind": "site",
            **site_row(session, site),
            "city": site.city,
            "tceq_rns": site.tceq_rns or [],
            "aliases": site.aliases or [],
        }
    if kind == RecordType.SIGNAL:
        sig = session.get(Signal, record_id)
        row = _signal_row(session, sig)
        return {
            "kind": "signal",
            "id": sig.id,
            "title": row["title"],
            "url": row["url"],
            "date": row["date"],
            "type": row["type"],
            "source_label": SOURCE_LABEL.get(sig.source, sig.source),
            "site": {"id": sig.site.id, "name": sig.site.name} if sig.site else None,
        }
    if kind == RecordType.ENTITY:
        entity = session.get(Entity, record_id)
        sites = []
        for link in session.scalars(
            select(Link).where(
                Link.from_type == RecordType.ENTITY,
                Link.from_id == entity.id,
                Link.to_type == RecordType.SITE,
                not_rejected(),
            )
        ):
            site = session.get(Site, link.to_id)
            role = ROLE_LABEL.get(str(link.link_type), str(link.link_type))
            if all(s["id"] != site.id for s in sites):
                sites.append({"id": site.id, "name": site.name, "role": role})
        companies = []
        for link in session.scalars(
            select(Link).where(
                Link.from_type == RecordType.COMPANY,
                Link.to_type == RecordType.ENTITY,
                Link.to_id == entity.id,
                not_rejected(),
            )
        ):
            company = session.get(Company, link.from_id)
            companies.append({"company": company.name, "confidence": str(link.confidence)})
        # Officers and registered agent are never returned (they can be people).
        return {
            "kind": "entity",
            "id": entity.id,
            "name": entity.legal_name,
            "sites": sites,
            "companies": companies,
        }
    company = session.get(Company, record_id)
    ticker = company.tickers[0].symbol if company.tickers else None
    return {
        "kind": "company",
        "id": company.id,
        "name": company.name,
        "ticker": ticker,
        "role": str(company.role),
    }


def open_links(session: Session) -> list[Link]:
    decided = select(ReviewDecision.link_id)
    links = session.scalars(
        select(Link)
        .where(Link.confidence == Confidence.LOW, Link.id.not_in(decided))
        .order_by(Link.id)
    ).all()
    return [link for link in links if link_kind(link) and _live(session, link)]


def _live(session: Session, link: Link) -> bool:
    """Skip links whose site was emptied by a merge, and self-links a merge left behind."""
    if link.from_type == link.to_type and link.from_id == link.to_id:
        return False
    merged = set(session.scalars(select(ReviewDecision.merged_site_id)))
    ends = [(link.from_type, link.from_id), (link.to_type, link.to_id)]
    return not any(t == RecordType.SITE and i in merged for t, i in ends)


def review_queue(session: Session, kind: str | None = None, limit: int = 200) -> dict:
    links = open_links(session)
    counts = {k: 0 for k in KINDS}
    for link in links:
        counts[link_kind(link)] += 1
    chosen = [link for link in links if kind is None or link_kind(link) == kind][:limit]
    items = [
        {
            "link_id": link.id,
            "kind": link_kind(link),
            "method": str(link.method),
            "note": link.note,
            "evidence_url": link.evidence_url,
            "created_at": link.created_at.date().isoformat() if link.created_at else None,
            "left": describe(session, link.from_type, link.from_id),
            "right": describe(session, link.to_type, link.to_id),
        }
        for link in chosen
    ]
    done = session.scalars(select(ReviewDecision).order_by(ReviewDecision.id.desc())).all()
    return {
        "counts": counts,
        "items": items,
        "decided": {
            "confirm": sum(d.decision == "confirm" for d in done),
            "reject": sum(d.decision == "reject" for d in done),
        },
    }


# --- applying a decision -------------------------------------------------------------------


def _merge_sites(session: Session, keep: Site, drop: Site, decided: Link) -> None:
    """Move everything from `drop` into `keep`. `drop` is left with no signals."""
    session.execute(update(Signal).where(Signal.site_id == drop.id).values(site_id=keep.id))
    session.execute(update(Equipment).where(Equipment.site_id == drop.id).values(site_id=keep.id))
    for link in session.scalars(
        select(Link).where(
            Link.id != decided.id,
            or_(
                (Link.from_type == RecordType.SITE) & (Link.from_id == drop.id),
                (Link.to_type == RecordType.SITE) & (Link.to_id == drop.id),
            ),
        )
    ):
        if link.from_type == RecordType.SITE and link.from_id == drop.id:
            link.from_id = keep.id
        if link.to_type == RecordType.SITE and link.to_id == drop.id:
            link.to_id = keep.id
    aliases = list(keep.aliases or [])
    for name in [drop.name, *(drop.aliases or [])]:
        if name != keep.name and name not in aliases:
            aliases.append(name)
    keep.aliases = aliases
    keep.tceq_rns = sorted(set(keep.tceq_rns or []) | set(drop.tceq_rns or []))
    keep.parcel_ids = sorted(set(keep.parcel_ids or []) | set(drop.parcel_ids or []))
    keep.county = keep.county or drop.county
    keep.city = keep.city or drop.city
    if keep.lat is None and drop.lat is not None:
        keep.lat, keep.lon = drop.lat, drop.lon
    if drop.status in PROGRESS and (
        keep.status not in PROGRESS or PROGRESS.index(drop.status) > PROGRESS.index(keep.status)
    ):
        keep.set_status(drop.status, note=f"merged with '{drop.name}' in review")
    drop.aliases, drop.tceq_rns = [], []
    session.flush()
    session.expire_all()


def _attach_filing(session: Session, sig: Signal, site: Site) -> None:
    """Put a filing on the site a reviewer chose, as the importer would have."""
    sig.site = site
    session.execute(update(Equipment).where(Equipment.signal_id == sig.id).values(site_id=site.id))
    fields = sig.extractions[-1].fields if sig.extractions else {}
    if site.county is None and fields.get("county"):
        site.county = fields["county"]
    if sig.type == SignalType.APPROVAL and site.status in (SiteStatus.RUMORED, SiteStatus.FILED):
        site.set_status(SiteStatus.APPROVED, signal=sig, note=f"{sig.title} (confirmed in review)")
    book = EntityBook(session)
    for owner in fields.get("owners") or []:
        link_party(
            session,
            book,
            owner,
            site,
            LinkType.OWNS,
            Confidence.HIGH,
            evidence_url=fields.get("url"),
            note=f"owner named in {sig.title} (filing confirmed in review)",
        )


def decide(session: Session, link_id: int, decision: str, note: str | None = None) -> dict:
    """Record a decision on one open low link. Returns what changed."""
    if decision not in DECISIONS:
        raise ReviewError(f"decision must be one of {DECISIONS}")
    link = session.get(Link, link_id)
    if link is None or link.confidence != Confidence.LOW or link_kind(link) is None:
        raise ReviewError(f"link {link_id} is not a low-confidence link in the queue")
    if session.scalar(select(ReviewDecision.id).where(ReviewDecision.link_id == link_id)):
        raise ReviewError(f"link {link_id} was already decided")
    note = (note or "").strip() or None
    row = ReviewDecision(link_id=link.id, decision=decision, note=note)
    out: dict = {"link_id": link.id, "decision": decision}
    if decision == "confirm":
        kind = link_kind(link)
        reason = "confirmed in review" + (f": {note}" if note else "")
        if kind == "same_site":
            a, b = session.get(Site, link.from_id), session.get(Site, link.to_id)
            count = {
                s.id: session.query(Signal).filter(Signal.site_id == s.id).count() for s in (a, b)
            }
            keep, drop = (a, b) if (count[a.id], -a.id) > (count[b.id], -b.id) else (b, a)
            _merge_sites(session, keep, drop, link)
            row.kept_site_id, row.merged_site_id = keep.id, drop.id
            out["site_id"] = keep.id
        elif kind == "filing":
            sig, site = session.get(Signal, link.from_id), session.get(Site, link.to_id)
            _attach_filing(session, sig, site)
            out["site_id"] = site.id
            for other in session.scalars(
                select(Link).where(
                    Link.id != link.id,
                    Link.from_type == RecordType.SIGNAL,
                    Link.from_id == sig.id,
                    Link.link_type == LinkType.SAME_SITE,
                    Link.confidence == Confidence.LOW,
                    Link.id.not_in(select(ReviewDecision.link_id)),
                )
            ):
                session.add(
                    ReviewDecision(
                        link_id=other.id, decision="reject", note=f"filing confirmed at {site.name}"
                    )
                )
        manual = Link(
            from_type=link.from_type,
            from_id=link.from_id if kind != "same_site" else row.merged_site_id,
            to_type=link.to_type,
            to_id=link.to_id if kind != "same_site" else row.kept_site_id,
            link_type=link.link_type,
            method=LinkMethod.MANUAL,
            confidence=Confidence.HIGH,
            evidence_url=link.evidence_url,
            note=reason,
        )
        session.add(manual)
        session.flush()
        row.manual_link_id = manual.id
    session.add(row)
    session.flush()
    return out


def merged_into(session: Session, site_id: int) -> dict | None:
    d = session.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.merged_site_id == site_id)
        .order_by(ReviewDecision.id.desc())
    )
    if d is None:
        return None
    seen = {site_id}
    target = d.kept_site_id
    while True:  # follow chains of merges
        nxt = session.scalar(
            select(ReviewDecision.kept_site_id)
            .where(ReviewDecision.merged_site_id == target)
            .order_by(ReviewDecision.id.desc())
        )
        if nxt is None or nxt in seen:
            break
        seen.add(target)
        target = nxt
    site = session.get(Site, target)
    return {"id": site.id, "name": site.name}


# --- the decisions file --------------------------------------------------------------------


@dataclass
class DecisionRow:
    decided_at: str
    decision: str
    kind: str
    link_type: str
    from_type: str
    from_ref: str
    to_type: str
    to_ref: str
    note: str


COLUMNS = [f.name for f in fields(DecisionRow)]


def decision_row(session: Session, link: Link, decision: str, note: str | None) -> DecisionRow:
    """Built before the decision is applied, while the link's ends are as the queue showed."""
    return DecisionRow(
        decided_at=datetime.now(UTC).date().isoformat(),
        decision=decision,
        kind=link_kind(link) or "",
        link_type=str(link.link_type),
        from_type=str(link.from_type),
        from_ref=record_ref(session, link.from_type, link.from_id),
        to_type=str(link.to_type),
        to_ref=record_ref(session, link.to_type, link.to_id),
        note=(note or "").strip(),
    )


def append_decision(row: DecisionRow, path: Path = DECISIONS_PATH) -> None:
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(asdict(row))


def read_decisions(path: Path = DECISIONS_PATH) -> list[DecisionRow]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [
            DecisionRow(**{c: (r.get(c) or "").strip() for c in COLUMNS})
            for r in csv.DictReader(f)
            if (r.get("decision") or "").strip()
        ]


def review_and_save(
    session: Session, link_id: int, decision: str, note: str | None, path: Path = DECISIONS_PATH
) -> dict:
    link = session.get(Link, link_id)
    if link is None:
        raise ReviewError(f"no link {link_id}")
    row = decision_row(session, link, decision, note)
    out = decide(session, link_id, decision, note)
    append_decision(row, path)
    return out


def _find_link(session: Session, row: DecisionRow) -> Link | None:
    from_type, to_type = RecordType(row.from_type), RecordType(row.to_type)
    from_id = resolve_ref(session, from_type, row.from_ref)
    to_id = resolve_ref(session, to_type, row.to_ref)
    if from_id is None or to_id is None:
        return None
    pairs = [(from_type, from_id, to_type, to_id)]
    if row.kind in ("same_site", "affiliate"):
        pairs.append((to_type, to_id, from_type, from_id))
    decided = select(ReviewDecision.link_id)
    for ft, fi, tt, ti in pairs:
        link = session.scalar(
            select(Link)
            .where(
                Link.from_type == ft,
                Link.from_id == fi,
                Link.to_type == tt,
                Link.to_id == ti,
                Link.link_type == LinkType(row.link_type),
                Link.confidence == Confidence.LOW,
                Link.id.not_in(decided),
            )
            .order_by(Link.id)
        )
        if link is not None:
            return link
    return None


def apply_decisions(session: Session, rows: list[DecisionRow]) -> list[str]:
    """Replay saved decisions (after a rebuild). Returns the ones that no longer match a link."""
    missing = []
    for row in rows:
        link = _find_link(session, row)
        if link is None:
            if row.kind == "filing" and row.decision == "reject":
                continue  # usually settled already by confirming another candidate
            missing.append(f"{row.decision} {row.from_ref} -> {row.to_ref}")
            continue
        decide(session, link.id, row.decision, row.note or None)
    return missing
