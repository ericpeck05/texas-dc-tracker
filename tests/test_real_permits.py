"""Regression tests on real TCEQ permits.

Drop a permit PDF in tests/fixtures/tceq/ next to a <name>.expected.json listing what you
found by hand. Each pair becomes a test. The equipment list is checked exactly: a missing
row fails, and so does an extra one, because false counts are worse than no counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracker.extract.tceq_air import extract_air_permit
from tracker.parsers.pdf import document_to_text

FIXTURES = Path(__file__).parent / "fixtures" / "tceq"
CASES = sorted(
    p
    for p in [*FIXTURES.glob("*.pdf"), *(FIXTURES / "local").glob("*.pdf")]
    if p.with_suffix(".expected.json").exists()
)
SCALAR_FIELDS = ("document_kind", "rn", "permit_number", "county", "site_name", "applicant")


def _equipment_key(item: dict) -> tuple:
    return (
        item["category"],
        item["count"],
        item.get("unit_size"),
        item.get("unit"),
        item.get("manufacturer"),
        item.get("model"),
    )


@pytest.mark.skipif(not CASES, reason="no real permit fixtures yet")
@pytest.mark.parametrize("pdf", CASES, ids=lambda p: p.stem)
def test_real_permit(pdf: Path):
    expected = json.loads(pdf.with_suffix(".expected.json").read_text())
    text, _ = document_to_text(pdf)
    result = extract_air_permit(text)

    for key in SCALAR_FIELDS:
        if key in expected:
            assert getattr(result, key) == expected[key], key
    if "nsr_authorizations" in expected:
        assert result.nsr_authorizations == expected["nsr_authorizations"]
    if "permits_by_rule" in expected:
        assert result.permits_by_rule == expected["permits_by_rule"]
    if "unit_groups" in expected:
        assert [g["unit_id"] for g in result.unit_groups] == expected["unit_groups"]

    if "equipment" in expected:
        got = sorted(
            (
                (e.category.value, e.count, e.unit_size, e.unit, e.manufacturer, e.model)
                for e in result.equipment
            ),
            key=str,
        )
        want = sorted((_equipment_key(i) for i in expected["equipment"]), key=str)
        assert got == want
