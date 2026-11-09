"""Records Online discovery, tested on trimmed copies of real result pages."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tracker.collectors.tceq.records_online import (
    RecordRow,
    RecordsOnline,
    ResultsFormatError,
    choose_documents,
    parse_results,
    plan_discovery,
)
from tracker.config import Settings
from tracker.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures" / "records_online"


def _row(d_id, title, when, project="p1", permit="1", name=None):
    return RecordRow(d_id, d_id, permit, project, title, date.fromisoformat(when), name)


def test_parse_real_permit_listing():
    rows, total = parse_results((FIXTURES / "permit_177262.html").read_text())
    assert total == 16 and len(rows) == 16
    review = next(r for r in rows if r.d_id == "8811645")
    assert (review.primary_id, review.secondary_id, review.title, review.begin_date) == (
        "177262",
        "391539",
        "Technical Review",
        date(2025, 5, 3),
    )
    assert review.entity_name is None  # reviews are filed with the site name blank
    assert "dID=8811645&Rendition=Web" in review.file_url


def test_parse_real_name_search_page_reports_total_across_pages():
    rows, total = parse_results((FIXTURES / "name_data_center_page1.html").read_text())
    assert total == 307
    assert rows[0].entity_name == "CORNERSTONE SOUTHWEST DATA CENTER"


def test_unrecognized_page_raises():
    with pytest.raises(ResultsFormatError):
        parse_results("<html>Service unavailable</html>")


def test_choose_real_177262_documents_matches_hand_picked():
    rows, _ = parse_results((FIXTURES / "permit_177262.html").read_text())
    chosen = choose_documents(rows)
    assert [(d.title, d.d_id) for d in chosen] == [
        ("Technical Review", "8811645"),
        ("Final Action", "8814054"),
    ]


def test_choose_skips_newer_project_without_review():
    rows = [
        _row("1", "Technical Review", "2025-01-01", project="a"),
        _row("2", "Final Action", "2025-01-02", project="a"),
        _row("3", "Final Action", "2025-06-01", project="b"),  # ownership change
        _row("4", "Initial Application", "2025-06-01", project="b"),
    ]
    assert [d.d_id for d in choose_documents(rows)] == ["1", "2"]


def test_choose_falls_back_to_newest_final_action():
    rows = [
        _row("1", "Final Action", "2024-01-01", project="a"),
        _row("2", "Final Action", "2025-01-01", project="b"),
    ]
    assert [d.d_id for d in choose_documents(rows)] == ["2"]


class FakeRO:
    def __init__(self, by_name, by_permit):
        self.by_name, self.by_permit = by_name, by_permit
        self.listed: list[str] = []

    def permits_by_name(self, keywords, *, since=None):
        return dict(self.by_name)

    def permit_documents(self, permit):
        self.listed.append(permit)
        return self.by_permit.get(permit, [])


def test_plan_skips_construction_sites_and_respects_limit():
    ro = FakeRO(
        {"1": "LONGHORN DATA CENTER", "2": "DPR DATA CENTER TEMPORARY BATCH PLANT", "3": "X DC"},
        {"1": [_row("9", "Technical Review", "2025-01-01")]},
    )
    plan = plan_discovery(ro, ["DATA CENTER"], max_permits=1)
    assert plan.skipped_construction == [("2", "DPR DATA CENTER TEMPORARY BATCH PLANT")]
    assert [(p.permit_number, [d.d_id for d in p.documents]) for p in plan.permits] == [
        ("1", ["9"])
    ]
    assert ro.listed == ["1"]


def test_search_gets_access_id_then_pages_until_total():
    page = (FIXTURES / "name_data_center_page1.html").read_text()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        service = parse_qs(urlsplit(str(request.url)).query).get("IdcService", [""])[0]
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if service == "TCEQ_SEARCH_ADD_ACCESS":
            return httpx.Response(200, json={"LocalData": {"accessID": "42"}})
        return httpx.Response(200, text=page)

    client = PoliteClient(
        Settings(min_request_interval=0),
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    rows = list(RecordsOnline(client).search("xRegEntName", "DATA CENTER"))

    searches = [
        parse_qs(urlsplit(str(r.url)).query)
        for r in requests
        if "TCEQ_PERFORM_SEARCH" in str(r.url)
    ]
    assert [q["StartRow"][0] for q in searches] == ["1", "101", "201", "301"]  # 307 total
    assert all(q["accessID"] == ["42"] for q in searches)
    assert "xRegEntName <substring> `DATA CENTER`" in searches[0]["QueryText"][0]
    assert len(rows) == 4 * 5
