import pytest
from sqlalchemy.exc import IntegrityError

from tracker.db.models import (
    Company,
    CompanyRole,
    Confidence,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    Site,
    SiteStatus,
    Ticker,
)


def test_status_changes_are_logged(session):
    site = Site(name="Test campus", county="Hunt")
    site.set_status(SiteStatus.FILED)
    site.set_status(SiteStatus.FILED)  # no-op
    site.set_status(SiteStatus.APPROVED)
    session.add(site)
    session.flush()
    changes = [(c.old_status, c.new_status) for c in site.status_changes]
    assert changes == [(None, SiteStatus.FILED), (SiteStatus.FILED, SiteStatus.APPROVED)]


def test_link_requires_evidence(session):
    session.add(
        Link(
            from_type=RecordType.SITE,
            from_id=1,
            to_type=RecordType.COMPANY,
            to_id=1,
            link_type=LinkType.OWNS,
            method=LinkMethod.NEWS,
            confidence=Confidence.LOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_company_ticker(session):
    msft = Company(name="Microsoft", role=CompanyRole.HYPERSCALER, is_public=True)
    msft.tickers.append(Ticker(symbol="MSFT", exchange="NASDAQ"))
    session.add(msft)
    session.flush()
    assert msft.tickers[0].company_id == msft.id
