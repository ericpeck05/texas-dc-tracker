"""Finding the manufacturer when the counting sentence does not name it."""

from __future__ import annotations

from datetime import date

from tracker.db.models import Equipment, EquipmentCategory, Signal, SignalType, Site
from tracker.db.session import init_db, make_engine, session_scope
from tracker.extract.tceq_air import document_makes, extract_air_permit
from tracker.reference.suppliers import find_supplier
from tracker.rollup import site_inventory


def _maker(text):
    found = find_supplier(text)
    return found[0].name if found else None


def test_model_families_identify_their_maker():
    assert _maker("Engine Model TM2500 PLUS") == "GE Vernova"
    assert _maker("LM2500XPRESS units") == "GE Vernova"
    assert _maker("driven by a 2,220 bhp Model QSK50-G5 engine") == "Cummins"
    assert _maker("Engine Model HSK78G") == "Cummins"
    assert _maker("G3520K Natural Gas-Fired Engines") == "Caterpillar"
    assert _maker("MTU Generators (Model 20V4000 DS3000)") == "Rolls-Royce (mtu)"
    assert _maker("twenty-five Bergen B36:45L9AG model engines") == "Bergen Engines"
    # Bare numbers and place names are not makers.
    assert _maker("Registration 3520 for 3516 Main St") is None
    assert _maker("Bergen County") is None


def test_document_with_one_maker_fills_unnamed_units():
    text = (
        "The proposed facility will include forty-four (44) natural gas fired engines used "
        "to generate electricity.\f"
        "Engine Model Bergen B36:45L9AG Allowable Emission Rate (lbs/hr): 16.11"
    )
    (eq,) = extract_air_permit(text).equipment
    assert (eq.count, eq.manufacturer, eq.model, eq.make_source) == (
        44,
        "Bergen Engines",
        "B36:45L9AG",
        "document",
    )


def test_two_makers_in_a_document_leave_units_unnamed():
    text = (
        "The site consists of 14 generator engines.\f MTU Generators (EPNs ENG1-ENG3; MTU Model "
        "20V4000 DS3000). Cummins Generators (EPNs ENG4-ENG15; Cummins Model QSK95-G9)."
    )
    makes = document_makes(text)
    assert set(makes["engine"]) == {"Rolls-Royce (mtu)", "Cummins"}
    gens = [e for e in extract_air_permit(text).equipment if e.count == 14]
    assert gens and gens[0].manufacturer is None and gens[0].make_source is None


def test_turbine_maker_is_not_given_to_engines_and_fire_pumps_are_ignored():
    text = (
        "The site will add six (6) emergency generators. The plant uses GE LM6000 combustion "
        "turbines. A Clarke diesel fire pump engine by Cummins is also onsite."
    )
    makes = document_makes(text)
    assert set(makes["turbine"]) == {"GE Vernova"} and makes["engine"] == {}
    gens = [e for e in extract_air_permit(text).equipment if e.count == 6]
    assert gens[0].manufacturer is None


def test_sentence_make_is_marked():
    (eq,) = extract_air_permit("One 1,500 kWe Cummins Model DQGAF emergency generator.").equipment
    assert (eq.manufacturer, eq.make_source) == ("Cummins", "sentence")


def test_rollup_borrows_make_from_another_document_for_the_permit(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        site = Site(name="S")
        session.add(site)

        def doc(when, rows):
            sig = Signal(
                site=site,
                type=SignalType.AIR_PERMIT,
                source="tceq_air",
                permit_number="181734",
                filing_date=date.fromisoformat(when),
            )
            session.add(sig)
            for count, make, model in rows:
                session.add(
                    Equipment(
                        signal=sig,
                        site=site,
                        category=EquipmentCategory.DIESEL_GENERATOR,
                        count=count,
                        manufacturer=make,
                        model=model,
                    )
                )
            return sig

        doc("2025-10-06", [(1, "Cummins", "QSK50-G5")])
        doc("2025-10-29", [(64, None, None)])  # newest: counts, but names no maker
        session.flush()
        (permit,) = site_inventory(session, site).permits
        (row,) = permit.rows
        assert permit.make(row) == ("Cummins", "QSK50-G5", "permit")


def test_turbine_model_beside_the_word_engine_is_still_a_turbine():
    text = (
        "This standard permit is to authorize six (6) 30.34 MW combustion turbines.\f"
        "Facility NOx Emissions Engine Model TM2500 GEN 8 Allowable Emission Rate"
    )
    makes = document_makes(text)
    assert makes["turbine"] == {"GE Vernova": {"TM2500"}} and makes["engine"] == {}
    (eq,) = extract_air_permit(text).equipment
    assert (eq.manufacturer, eq.model, eq.make_source) == ("GE Vernova", "TM2500", "document")


def test_two_makers_split_by_one_mention_per_unit():
    rows = " ".join(f"EGEN{n:02d} / Caterpillar 0.56 0.01 Generator" for n in range(8, 21))
    text = (
        "The Facility has seven existing 2.0-MW Cummins standby generators. This application "
        "makes the following changes: Addition of 13 diesel-fired emergency generators.\f"
        f"New Emissions {rows}"
    )
    eq = {e.count: e for e in extract_air_permit(text).equipment}
    assert (eq[7].manufacturer, eq[7].make_source) == ("Cummins", "sentence")
    assert (eq[13].manufacturer, eq[13].make_source) == ("Caterpillar", "document")


def test_small_groups_are_not_matched_by_mention_count():
    text = (
        "Addition of 2 diesel-fired emergency generators.\f"
        "Cummins generator and Caterpillar generator. Caterpillar generator again."
    )
    (eq,) = extract_air_permit(text).equipment
    assert eq.manufacturer is None


def test_total_followed_by_sized_parts_counts_the_parts():
    text = (
        "The proposed facility will include forty-four (44) natural gas fired engines used to "
        "generate electricity. These engines include: Twenty-five (25) natural gas fired Bergen "
        "B36:45L9AG model engines with approximately 5.18 MW capacity each. Nineteen (19) "
        "natural gas fired Bergen B36:45V20 model engines with approximately 12.63 MW capacity "
        "each."
    )
    rows = [(e.count, e.unit_size, e.model, e.role) for e in extract_air_permit(text).equipment]
    assert rows == [
        (44, None, None, "subset"),
        (25, 5.18, "B36:45L9AG", "added"),
        (19, 12.63, "B36:45V20", "added"),
    ]
