"""Extractor tests on synthetic permit language modeled on TCEQ phrasing.

These sentences are written for the tests. Real permits are tested in test_real_permits.py.
"""

from __future__ import annotations

from tracker.db.models import EquipmentCategory as Cat
from tracker.extract.tceq_air import extract_air_permit


def one(text: str):
    eq = extract_air_permit(text).equipment
    assert len(eq) == 1, eq
    return eq[0]


def test_word_and_paren_count_with_kw_size():
    e = one("The site will operate eighteen (18) 2,500-kW diesel-fired emergency generators.")
    assert (e.count, e.unit_size, e.unit, e.fuel, e.category) == (
        18,
        2500,
        "kW",
        "diesel",
        Cat.DIESEL_GENERATOR,
    )


def test_make_model_and_trailing_rating():
    e = one(
        "Thirty-two (32) Caterpillar 3516C diesel emergency generator engines, "
        "each rated at 2,682 bhp, will be installed."
    )
    assert e.count == 32
    assert (e.manufacturer, e.model, e.ticker) == ("Caterpillar", "3516C", "CAT")
    assert (e.unit_size, e.unit) == (2682, "hp")


def test_turbine_with_ge_model_maps_to_gev():
    e = one("The applicant proposes five (5) GE LM6000PF natural gas-fired combustion turbines.")
    assert e.category == Cat.GAS_TURBINE
    assert (e.count, e.manufacturer, e.model, e.ticker, e.fuel) == (
        5,
        "GE Vernova",
        "LM6000PF",
        "GEV",
        "natural_gas",
    )


def test_megawatt_decimal_size_and_bare_count():
    e = one("Authorization covers 24 Cummins QSK95 3.0 MW emergency generators.")
    assert (e.count, e.unit_size, e.unit, e.manufacturer, e.model) == (
        24,
        3.0,
        "MW",
        "Cummins",
        "QSK95",
    )
    assert e.category == Cat.GENERATOR  # fuel not stated, so not guessed


def test_two_items_in_one_sentence_do_not_bleed():
    eq = extract_air_permit(
        "The facility includes twenty (20) diesel generators and two (2) 50 MW Solar Titan 130 "
        "turbines firing pipeline quality natural gas."
    ).equipment
    gens, turbines = eq
    assert (gens.count, gens.unit_size, gens.manufacturer) == (20, None, None)
    assert (turbines.count, turbines.unit_size, turbines.unit) == (2, 50, "MW")
    assert (turbines.manufacturer, turbines.model, turbines.ticker) == (
        "Solar Turbines",
        "Titan 130",
        "CAT",
    )


def test_model_number_is_not_mistaken_for_count():
    e = one("Install five (5) Solar Titan 130 turbines.")
    assert e.count == 5


def test_rule_number_is_not_a_count():
    assert (
        extract_air_permit(
            "Authorized under Permit by Rule 106.511 for emergency generators."
        ).equipment
        == []
    )


def test_uncounted_mentions_are_skipped():
    assert (
        extract_air_permit(
            "The generators shall not operate more than 100 hours per year."
        ).equipment
        == []
    )


def test_tank_uses_gallons_not_power():
    e = one("Two (2) 20,000-gallon diesel storage tanks serve the generators.")
    assert (e.category, e.count, e.unit_size, e.unit) == (Cat.FUEL_TANK, 2, 20000, "gal")


def test_fire_pump_is_other():
    e = one("One (1) 300 hp diesel fire pump engine.")
    assert e.category == Cat.OTHER


def test_turbine_generator_phrase_counted_once():
    eq = extract_air_permit("Four (4) combustion turbine generators rated at 35 MW each.").equipment
    assert len(eq) == 1 and eq[0].count == 4 and eq[0].unit_size == 35


def test_repeated_mention_is_deduplicated():
    text = (
        "Ten (10) 2,000 kW diesel generators are proposed. "
        "Emissions from the ten (10) 2,000 kW diesel generators are below limits."
    )
    result = extract_air_permit(text)
    assert len(result.equipment) == 1
    assert result.duplicate_mentions == 1


def test_page_numbers_follow_form_feeds():
    result = extract_air_permit("Cover page.\fThree (3) 1,500 kW diesel generators.")
    assert result.equipment[0].page == 2


def test_metadata_fields():
    text = (
        "Applicant: Example Data Campus LLC\n"
        "Regulated Entity Number: RN112345678  Customer Number: CN601234567\n"
        "Air Quality Permit Number 176543\n"
        "The facility is located in Ellis County, Texas. Ellis County is ...\n"
    )
    r = extract_air_permit(text)
    assert (r.rn, r.cn, r.permit_number, r.county, r.applicant) == (
        "RN112345678",
        "CN601234567",
        "176543",
        "Ellis",
        "Example Data Campus LLC",
    )


def test_snippet_keeps_source_sentence():
    e = one("Background text. Six (6) 2,750 kW diesel generators will be installed. More text.")
    assert "Six (6) 2,750 kW diesel generators" in e.snippet
