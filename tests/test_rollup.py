from tracker.db.models import Equipment, EquipmentCategory, Signal, SignalType, Site
from tracker.rollup import site_inventory


def _signal(session, site, permit, when, rows):
    from datetime import date

    sig = Signal(
        site=site,
        type=SignalType.AIR_PERMIT,
        source="tceq_air",
        permit_number=permit,
        filing_date=date.fromisoformat(when) if when else None,
    )
    session.add(sig)
    for count, size, method in rows:
        session.add(
            Equipment(
                signal=sig,
                site=site,
                category=EquipmentCategory.GAS_TURBINE,
                count=count,
                unit_size=size,
                unit="MW" if size else None,
                method=method,
            )
        )
    session.flush()
    return sig


def test_each_permit_counts_once_and_table_beats_newer_prose(session):
    site = Site(name="Example", county="Taylor")
    session.add(site)
    table = _signal(session, site, "177263", "2025-01-22", [(5, None, "maert"), (5, None, "maert")])
    _signal(session, site, "177263", "2025-02-01", [(5, 38.0, "prose"), (5, 34.1, "prose")])
    _signal(session, site, "177262", "2024-09-05", [(10, 2.0, "prose")])
    newest = _signal(session, site, "177262", "2025-04-24", [(12, 2.0, "prose")])

    inv = site_inventory(session, site)
    chosen = {p.permit_number: p.signal for p in inv.permits}
    assert chosen == {"177262": newest, "177263": table}
    assert all(p.other_documents == 1 for p in inv.permits)
    assert inv.totals()["gas_turbine"] == {"count": 22, "mw": 24.0}


def test_table_rows_borrow_sizes_from_same_permit_same_model(session):
    site = Site(name="Example", county="Taylor")
    session.add(site)
    table = _signal(session, site, "177263", "2025-01-22", [(5, None, "maert")])
    table.equipment[0].model = "Titan 350"
    review = _signal(session, site, "177263", "2025-01-22", [(5, 38.0, "count_change")])
    review.equipment[0].model = "Titan 350"
    session.flush()

    inv = site_inventory(session, site)
    (permit,) = inv.permits
    assert permit.signal is table
    assert permit.size(table.equipment[0]) == (38.0, "MW")
    assert inv.totals()["gas_turbine"] == {"count": 5, "mw": 190.0}


def _roles(session, site, permit, when, rows, withdrawn=False):
    from tracker.db.models import Extraction

    sig = _signal(session, site, permit, when, [(c, None, "prose") for c, _ in rows])
    for e, (_, role) in zip(sig.equipment, rows, strict=True):
        e.role = role
    session.add(
        Extraction(
            signal=sig, extractor="t", extractor_version="0", fields={"withdrawn": withdrawn}
        )
    )
    session.flush()
    return sig


def test_rollup_nets_existing_added_removed(session):
    site = Site(name="Example")
    session.add(site)
    _roles(session, site, "1", "2025-01-01", [(33, "existing"), (32, "removed"), (56, "added")])
    assert site_inventory(session, site).totals()["gas_turbine"]["count"] == 57


def test_rollup_uses_only_total_rows_when_present(session):
    site = Site(name="Example")
    session.add(site)
    _roles(session, site, "1", "2025-01-01", [(146, "total"), (110, "unknown"), (36, "added")])
    assert site_inventory(session, site).totals()["gas_turbine"]["count"] == 146


def test_rollup_skips_withdrawn_permit(session):
    site = Site(name="Example")
    session.add(site)
    _roles(session, site, "1", "2025-01-01", [(10, "unknown")])
    _roles(session, site, "2", "2026-04-20", [(99, "unknown")], withdrawn=True)
    inv = site_inventory(session, site)
    assert inv.withdrawn_permits == ["2"]
    assert inv.totals()["gas_turbine"]["count"] == 10


def test_total_applies_per_category(session):
    site = Site(name="Example")
    session.add(site)
    sig = _roles(session, site, "1", "2025-01-01", [(14, "total"), (14, "unknown")])
    sig.equipment[1].category = EquipmentCategory.FUEL_TANK
    session.flush()
    totals = site_inventory(session, site).totals()
    assert totals["gas_turbine"]["count"] == 14 and totals["fuel_tank"]["count"] == 14
