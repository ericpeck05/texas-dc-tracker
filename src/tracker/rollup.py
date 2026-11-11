"""Current equipment per site, counting each unit once.

A site collects many filings about the same equipment: a permit letter, its technical
review, later revisions. Adding their equipment rows together would count the same turbine
two or three times. Instead, each permit contributes the rows of one document, its best:

1. A document with an emission rates table (MAERT) wins, because it lists every unit.
2. Otherwise the most recent document wins (by its latest date).
3. On the same date a technical review beats its cover letter, then the last ingested.

Within that document, rows are netted by what the permit says about them:

- If a category has a "total" row ("Upon authorization, the site will include ..."), only
  total rows count for it, because the other sentences describe parts of that total.
- "Subset" rows ("only 5 engines will be operating") never count.
- Otherwise existing + added + unlabeled rows count, and removed rows are subtracted
  ("remove 32 of the 33 existing generators and add 56").

A permit whose newest document is a withdrawn application contributes nothing.

Known limit: a revision that lists only what changed, with no existing or total figures,
undercounts. Every count stays traceable to its sentence, so check surprising totals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import Equipment, EquipmentCategory, Signal, Site


@dataclass
class PermitInventory:
    permit_number: str | None
    signal: Signal  # the document the counts come from
    rows: list[Equipment]
    other_documents: int  # documents for this permit that were not used
    # Unit sizes the counting document leaves out (a MAERT has none), taken from another
    # document for the same permit with the same category, make, and model.
    borrowed_sizes: dict[int, Equipment] = field(default_factory=dict)
    # Makes the counting document leaves out, from another document for the same permit
    # that names exactly one maker for that kind of equipment: row id -> (make, model).
    borrowed_makes: dict[int, tuple[str, str | None]] = field(default_factory=dict)

    def make(self, row: Equipment) -> tuple[str | None, str | None, str | None]:
        """(manufacturer, model, where the make came from)."""
        if row.manufacturer:
            return row.manufacturer, row.model, row.make_source or "sentence"
        if row.id in self.borrowed_makes:
            make, model = self.borrowed_makes[row.id]
            return make, row.model or model, "permit"
        return None, row.model, None

    def size(self, row: Equipment) -> tuple[float | None, str | None]:
        src = self.borrowed_sizes.get(row.id, row)
        return src.unit_size, src.unit

    def sign(self, row: Equipment) -> int:
        """+1 counted, -1 subtracted (removed units), 0 not counted (part of a total)."""
        if row.method == "maert":
            return 1
        if row.role == "subset":
            return 0
        has_total = any(
            r.role == "total" and r.category == row.category
            for r in self.rows
            if r.method != "maert"
        )
        if has_total:
            return 1 if row.role == "total" else 0
        return -1 if row.role == "removed" else 1


@dataclass
class SiteInventory:
    site: Site
    permits: list[PermitInventory] = field(default_factory=list)
    withdrawn_permits: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, dict[str, float]]:
        """Per category: unit count and total MW (where sizes are in kW or MW)."""
        out: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "mw": 0.0})
        for permit in self.permits:
            for row in permit.rows:
                sign = permit.sign(row)
                if not sign:
                    continue
                t = out[str(row.category)]
                t["count"] += sign * row.count
                size, unit = permit.size(row)
                if size and unit == "MW":
                    t["mw"] += sign * row.count * size
                elif size and unit == "kW":
                    t["mw"] += sign * row.count * size / 1000
        return dict(out)


def _family(category: EquipmentCategory) -> str | None:
    if category is EquipmentCategory.GAS_TURBINE:
        return "turbine"
    if category in (
        EquipmentCategory.GENERATOR,
        EquipmentCategory.DIESEL_GENERATOR,
        EquipmentCategory.GAS_GENERATOR,
    ):
        return "engine"
    return None


def _withdrawn(signal: Signal) -> bool:
    return any(ex.fields.get("withdrawn") for ex in signal.extractions)


def _rank(signal: Signal) -> tuple:
    """Table first, then the newest document, and on the same date a technical review over
    its cover letter (the review carries the unit list)."""
    has_table = any(e.method == "maert" for e in signal.equipment)
    kind = next((ex.fields.get("document_kind") for ex in signal.extractions), None) or ""
    return (has_table, signal.filing_date or date.min, kind.endswith("technical_review"), signal.id)


def site_inventory(session: Session, site: Site) -> SiteInventory:
    signals = session.scalars(select(Signal).where(Signal.site_id == site.id)).all()
    by_permit: dict[str | None, list[Signal]] = defaultdict(list)
    for sig in signals:
        by_permit[sig.permit_number].append(sig)

    inventory = SiteInventory(site)
    for permit_number, all_sigs in sorted(by_permit.items(), key=lambda kv: str(kv[0])):
        newest = max(all_sigs, key=lambda s: (s.filing_date or date.min, s.id))
        if any(_withdrawn(s) for s in all_sigs if s.filing_date == newest.filing_date):
            inventory.withdrawn_permits.append(permit_number or "?")
            continue
        sigs = [s for s in all_sigs if s.equipment and not _withdrawn(s)]
        if not sigs:
            continue
        best = max(sigs, key=_rank)
        permit = PermitInventory(permit_number, best, list(best.equipment), len(sigs) - 1)
        others = sorted((s for s in sigs if s is not best), key=_rank, reverse=True)
        for row in permit.rows:
            if row.unit_size or not row.model:
                continue
            for sig in others:
                match = next(
                    (
                        e
                        for e in sig.equipment
                        if e.unit_size
                        and (e.category, e.manufacturer, e.model)
                        == (row.category, row.manufacturer, row.model)
                    ),
                    None,
                )
                if match:
                    permit.borrowed_sizes[row.id] = match
                    break
        for row in permit.rows:
            family = _family(row.category)
            if row.manufacturer or family is None:
                continue
            named = {
                (e.manufacturer, e.model)
                for sig in others
                for e in sig.equipment
                if e.manufacturer and _family(e.category) == family
            }
            makers = {make for make, _ in named}
            if len(makers) == 1:
                make = makers.pop()
                models = {model for mk, model in named if mk == make and model}
                permit.borrowed_makes[row.id] = (make, models.pop() if len(models) == 1 else None)
        inventory.permits.append(permit)
    return inventory
