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


# Patterns found in a real Title V Statement of Basis (Longhorn Data Center, O4721).


def test_title_v_attribute_numbers_are_not_counts():
    text = (
        "Heat Input = Turbine has a heat input at peak load of at least 50 MMBtu/hr but less "
        "than 850 MMBtu/hr. 75% of Peak = The combustion turbine does not operate at less than "
        "75% of peak load. 30 MW = The combustion turbine has an output of greater than 30 MW. "
        "GRP-TURB1 30 TAC Chapter 111 Visible Emissions = not a steam generator. "
        "Standard = The emergency CI ICE meets the Tier 1, 2, 3, or 4 standards applicable to "
        "non-emergency engines. Page 11 of 23 generators. Subpart 60KKKK-01 Unit Type = "
        "Simple Combustion Turbine."
    )
    assert extract_air_permit(text).equipment == []


def test_count_does_not_cross_sentences():
    text = "The tank may store two different products. The tank may need two sets of rules."
    assert extract_air_permit(text).equipment == []


def test_unit_group_ids_are_not_nouns_but_are_recorded():
    r = extract_air_permit("GRP-TANKS 40 CFR Part 60. GRP-GEN1 40 CFR Part 60.\fGRP-TURB2 x")
    assert r.equipment == []
    assert [(g["unit_id"], g["category"], g["pages"]) for g in r.unit_groups] == [
        ("GRP-GEN1", "generator", [1]),
        ("GRP-TANKS", "fuel_tank", [1]),
        ("GRP-TURB2", "gas_turbine", [2]),
    ]


def test_title_v_labeled_fields_win():
    text = (
        "Statement of Basis of the Federal Operating Permit\n"
        "Example Campus Association\n"
        "Site Name: Example Data Center\n"
        "Nearest City: Abilene\n"
        "County: Taylor\n"
        "Permit Number: O4721\n"
        "Authorization No.: 177263 Issuance Date: 01/22/2025\n"
        "Number: 106.511 Version No./Date: 09/04/2000\n"
    )
    r = extract_air_permit(text)
    assert (r.document_kind, r.county, r.site_name, r.nearest_city, r.permit_number) == (
        "title_v_statement_of_basis",
        "Taylor",
        "Example Data Center",
        "Abilene",
        "O4721",
    )
    assert r.applicant == "Example Campus Association"
    assert r.nsr_authorizations == [{"number": "177263", "issued": "2025-01-22"}]
    assert r.permits_by_rule == ["106.511"]


# Patterns found in NSR standard permit 177263 (Longhorn Data Center turbines).


def test_model_number_after_family_name_is_not_a_count():
    text = "Install Titan 350 simple-cycle turbines at the site."
    assert extract_air_permit(text).equipment == []


def test_address_number_does_not_cross_colon():
    text = "255 FILLMORE ST DENVER CO 80206-5030 Re: Electric Generating Units Standard Permit"
    assert extract_air_permit(text).equipment == []


def test_count_change_uses_new_count_and_item_size():
    text = (
        "Reduce the count of Titan 350 simple-cycle turbines from six to five and increase the "
        "site-rated power to 38 MW per turbine. Reduce the count of GE LM2500 simple-cycle "
        "turbines from six to five, reduce the site-rated power to 34.1 MW per turbine."
    )
    eq = extract_air_permit(text).equipment
    got = [(e.method, e.count, e.unit_size, e.unit, e.manufacturer, e.model) for e in eq]
    assert got == [
        ("count_change", 5, 38.0, "MW", "Solar Turbines", "Titan 350"),
        ("count_change", 5, 34.1, "MW", "GE Vernova", "LM2500"),
    ]


def test_maert_rows_count_units_and_replace_prose():
    text = (
        "This registration is for the 4 natural gas simple-cycle turbines at the site.\f"
        "MAXIMUM ALLOWABLE EMISSION RATES TABLE (MAERT)\n"
        "EPN / Emission Source VOC NOx\n"
        "Turbine 1 / Titan 350 simple-cycle 5.24 22.96\n"
        "Turbine 2 / Titan 350 simple-cycle 5.24 22.96\n"
        "Turbine 3 / GE LM2500 simple-cycle 5.41 23.70\n"
        "Turbine 4 / GE LM2500 simple-\n"
        "5.41 23.70\n"
        "cycle\n"
        "FUG / Fugitives <0.01 0.01\n"
    )
    r = extract_air_permit(text)
    got = sorted((e.method, e.count, e.manufacturer, e.model, e.page) for e in r.equipment)
    assert got == [
        ("maert", 2, "GE Vernova", "LM2500", 2),
        ("maert", 2, "Solar Turbines", "Titan 350", 2),
    ]
    assert [m.count for m in r.superseded_mentions] == [4]


def test_table_style_labels_without_colons():
    text = (
        "Electric Generating Unit Standard Permit\n"
        "Technical Review\n"
        "Company Example DC 1, LLC Registration Number 177263\n"
        "County Taylor Regulated Entity Number RN112029079\n"
        "Site Name Example Data Center\n"
    )
    r = extract_air_permit(text)
    assert (r.document_kind, r.applicant, r.permit_number, r.county, r.site_name) == (
        "nsr_technical_review",
        "Example DC 1, LLC",
        "177263",
        "Taylor",
        "Example Data Center",
    )


# Patterns found in PBR registration 177262 (Longhorn Data Center diesel generators).


def test_spelled_out_megawatts_and_baudouin():
    text = (
        "Reduce hours for each three Tier II Caterpillar C175 3-megawatt (MW) diesel generators; "
        "Add 28 Tier IV Baudouin 2.8 MW diesel generators."
    )
    got = [
        (e.count, e.unit_size, e.unit, e.manufacturer, e.model, e.ticker)
        for e in extract_air_permit(text).equipment
    ]
    assert got == [
        (3, 3.0, "MW", "Caterpillar", "C175", "CAT"),
        (28, 2.8, "MW", "Baudouin", None, "2338"),
    ]


def test_change_item_ends_at_semicolon_and_add_a_counts_one():
    text = (
        "Increase the count of diesel belly tanks from 18 to 62; "
        "Add a 40,000-barrel floating roof diesel tank."
    )
    got = [(e.method, e.count, e.unit_size, e.unit) for e in extract_air_permit(text).equipment]
    assert got == [("count_change", 62, None, None), ("prose", 1, 40000.0, "bbl")]


def test_add_a_needs_an_equipment_phrase():
    assert extract_air_permit("Add a note to the file about the generators.").equipment == []


def test_pbr_labels_and_document_date_skips_future_dates():
    text = (
        "Permit by Rule (PBR) Registration\n"
        "Technical Review\n"
        "Unit Name: Example Data Center Customer Reference No.: CN606295137\n"
        "PBR No(s).: Revised: 106.472, 106.511 Project Received Date: April 11, 2025\n"
        "Expiration Date: August 21, 2034\n"
        "04/24/2025 Reviewer Date\n"
    )
    r = extract_air_permit(text)
    assert (r.document_kind, r.site_name, r.permits_by_rule, r.document_date) == (
        "pbr_technical_review",
        "Example Data Center",
        ["106.472", "106.511"],
        "2025-04-24",
    )


# Patterns found auditing the first statewide discovery run (Nov 2026).


def _rows(text):
    return [
        (e.count, str(e.category), e.unit_size, e.unit, e.role)
        for e in extract_air_permit(text).equipment
    ]


def test_rule_paragraph_numbers_are_not_counts():
    text = (
        "(3) The capacity of any tank does not exceed 25,000 gallons. "
        "(2) For any engine rated 500 hp or greater, subparagraphs apply. "
        "(6) Engines (A) This standard permit authorizes emissions from an engine."
    )
    assert extract_air_permit(text).equipment == []


def test_add_an_additional_n_uses_n():
    assert _rows("The site plans to add an additional 64 diesel-fired emergency generators.") == [
        (64, "diesel_generator", None, None, "added")
    ]


def test_one_for_each_and_days_are_not_counts():
    text = (
        "Sixty-two diesel belly tanks; one for each diesel generator set unit. "
        "Notify within ten days after construction begins. Engines and turbines are listed. "
        "Test within 60 days following engine start-up."
    )
    assert _rows(text) == [(62, "fuel_tank", None, None, "unknown")]


def test_unit_label_ranges_are_not_counts():
    assert (
        extract_air_permit("Gen 201 - Gen 224 / 3,250 kWe Emergency Generator 2.64").equipment == []
    )


def test_a_total_of_n_and_consists_of_n():
    text = (
        "The site consists of 14 generator engines authorized under PBR 106.511. "
        "A total of 24 identical sub-base diesel storage tanks are installed."
    )
    assert [(c, cat, role) for c, cat, _, _, role in _rows(text)] == [
        (14, "generator", "total"),
        (24, "fuel_tank", "total"),
    ]


def test_restatement_is_merged_into_one_row():
    text = (
        "Microsoft is installing two (2) 500 kW Caterpillar Generators. "
        "The two 500 kW generators are Tier 3 certified."
    )
    eq = extract_air_permit(text).equipment
    assert [(e.count, e.unit_size, e.manufacturer, e.role) for e in eq] == [
        (2, 500.0, "Caterpillar", "added")
    ]


def test_paired_counts_with_sizes():
    text = (
        "Authorize six (6) 30.34 MW and six (6) 25.04 MW pipeline-quality natural gas powered "
        "simple-cycle combustion turbines."
    )
    assert sorted((c, s) for c, _, s, _, _ in _rows(text)) == [(6, 25.04), (6, 30.34)]


def test_existing_added_removed_roles():
    text = (
        "Serverfarm is proposing to remove 32 of the 33 existing generators and construct an "
        "additional 56 diesel-fired emergency generators."
    )
    assert sorted((c, role) for c, _, _, _, role in _rows(text)) == [
        (32, "removed"),
        (33, "existing"),
        (56, "added"),
    ]


def test_same_make_and_size_with_different_unit_labels_stay_separate():
    text = (
        "- Three Tier II 3 MW Caterpillar C175 emergency diesel generators (EPNs GEN-1 through "
        "GEN-3) - Three Tier II 3 MW Caterpillar C175 black-start diesel generators (EPNs GEN-4 "
        "through GEN-6)"
    )
    eq = extract_air_permit(text).equipment
    assert [(e.count, e.epns) for e in eq] == [(3, "GEN-1..GEN-3"), (3, "GEN-4..GEN-6")]


def test_withdrawn_application_is_flagged():
    text = "Project Type Initial/Withdrawal Customer Reference Number CN606400323\n"
    assert extract_air_permit(text).withdrawn
    assert not extract_air_permit("Project Type Revision\n").withdrawn


def test_total_cue_before_colon():
    text = (
        "Upon authorization, the site will include the following emission sources: "
        "One hundred and ten (110) Tier II diesel generators, Thirty-six (36) Tier IV diesel "
        "generators, One hundred and forty-six (146) diesel belly tanks."
    )
    assert {role for *_, role in _rows(text)} == {"total"}


def test_operating_subset_is_not_more_units():
    text = (
        "Circe is submitting this registration for the installation of seven (7) natural gas "
        "internal combustion engine generators. Furthermore, only 5 engines will be operating "
        "with 2 engines serving as backup at any given time."
    )
    assert sorted((c, role) for c, _, _, _, role in _rows(text)) == [
        (2, "subset"),
        (5, "subset"),
        (7, "added"),
    ]


def test_earlier_filing_restating_existing_units_is_merged():
    text = (
        "Vantage owns and operates 22 diesel-fired emergency generators at the site. "
        "In February 2024, Vantage submitted a PBR registration to construct and operate the "
        "first 22 diesel generators on site."
    )
    assert _rows(text) == [(22, "diesel_generator", None, None, "existing")]


def test_one_or_more_is_not_a_count():
    text = "Plants that include one or more stationary compression ignition engines shall comply."
    assert extract_air_permit(text).equipment == []


def test_partial_emission_table_is_not_trusted():
    text = (
        "The site has 13 new diesel-fired emergency generators.\f"
        "MAXIMUM ALLOWABLE EMISSION RATES TABLE\n"
        "EGEN08 / Caterpillar 1.10 2.20\n"
        "EGEN09 / Caterpillar 1.10 2.20\n"
    )
    r = extract_air_permit(text)
    assert [(e.method, e.count) for e in r.equipment] == [("prose", 13)]
    assert [m.count for m in r.partial_tables] == [2]


def test_sentence_break_right_before_the_noun():
    text = "Emissions use AP-42 2 factors (all other HAPs). Engines are equipped with catalysts."
    assert extract_air_permit(text).equipment == []


def test_remaining_unit_after_tank_mention_is_subset():
    text = (
        "One existing 1.0- Megawatt (MW) Cummins administrative generator with its "
        "1,200-gallon capacity belly tank will remain at the Facility."
    )
    roles = {(str(e.category), e.role) for e in extract_air_permit(text).equipment}
    assert ("generator", "subset") in roles
