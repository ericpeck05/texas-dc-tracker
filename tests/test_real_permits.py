"""Regression tests on real TCEQ permits.

Drop a permit PDF in tests/fixtures/tceq/ next to a <name>.expected.json listing the
equipment you counted by hand. Each pair becomes a test. Skipped until fixtures exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracker.extract.tceq_air import extract_air_permit
from tracker.parsers.pdf import document_to_text

FIXTURES = Path(__file__).parent / "fixtures" / "tceq"
CASES = sorted(p for p in FIXTURES.glob("*.pdf") if p.with_suffix(".expected.json").exists())


@pytest.mark.skipif(not CASES, reason="no real permit fixtures yet")
@pytest.mark.parametrize("pdf", CASES, ids=lambda p: p.stem)
def test_real_permit(pdf: Path):
    expected = json.loads(pdf.with_suffix(".expected.json").read_text())
    text, _ = document_to_text(pdf)
    result = extract_air_permit(text)

    for key in ("rn", "permit_number", "county"):
        if key in expected:
            assert getattr(result, key) == expected[key], key

    got = {
        (e.category.value, e.count, e.unit_size, e.unit, e.manufacturer, e.model)
        for e in result.equipment
    }
    for item in expected.get("equipment", []):
        want = (
            item["category"],
            item["count"],
            item.get("unit_size"),
            item.get("unit"),
            item.get("manufacturer"),
            item.get("model"),
        )
        assert want in got, f"missing {want}; got {sorted(got, key=str)}"
