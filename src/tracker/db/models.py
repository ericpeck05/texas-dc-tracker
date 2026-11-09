"""Database schema. Everything hangs off Site; every filing is a Signal; every link has evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class SiteStatus(StrEnum):
    RUMORED = "rumored"
    FILED = "filed"
    APPROVED = "approved"
    UNDER_CONSTRUCTION = "under_construction"
    OPERATING = "operating"
    CANCELLED = "cancelled"


class SignalType(StrEnum):
    AIR_PERMIT = "air_permit"
    STORMWATER = "stormwater"
    APPROVAL = "approval"
    INCENTIVE = "incentive"
    GRID = "grid"
    SEC_MENTION = "sec_mention"


class EquipmentCategory(StrEnum):
    DIESEL_GENERATOR = "diesel_generator"
    GAS_GENERATOR = "gas_generator"  # reciprocating engines on natural gas
    GENERATOR = "generator"  # fuel not stated in the filing
    GAS_TURBINE = "gas_turbine"
    FUEL_TANK = "fuel_tank"
    OTHER = "other"


class CompanyRole(StrEnum):
    HYPERSCALER = "hyperscaler"
    DEVELOPER = "developer"
    NEOCLOUD = "neocloud"
    UTILITY = "utility"
    SUPPLIER = "supplier"
    REIT = "reit"
    OTHER = "other"


class RecordType(StrEnum):
    SITE = "site"
    SIGNAL = "signal"
    ENTITY = "entity"
    COMPANY = "company"
    TICKER = "ticker"
    EQUIPMENT = "equipment"


class LinkType(StrEnum):
    OWNS = "owns"
    OCCUPIES = "occupies"
    OPERATES = "operates"
    DEVELOPS = "develops"
    FILED_BY = "filed_by"
    PARENT_OF = "parent_of"
    AFFILIATE_OF = "affiliate_of"
    SUPPLIES = "supplies"
    SAME_SITE = "same_site"


class LinkMethod(StrEnum):
    FILING = "filing"
    ADDRESS_MATCH = "address_match"
    AGENT_MATCH = "agent_match"
    NAME_MATCH = "name_match"  # same legal or project name in two filings
    NEWS = "news"
    LLM = "llm"
    MANUAL = "manual"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _enum(enum_cls: type[StrEnum]) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Site(Base):
    __tablename__ = "site"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    aliases: Mapped[list[Any]] = mapped_column(default=list)
    county: Mapped[str | None] = mapped_column(String(64), index=True)
    city: Mapped[str | None] = mapped_column(String(128))  # None = unincorporated
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    parcel_ids: Mapped[list[Any]] = mapped_column(default=list)
    tceq_rns: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[SiteStatus] = mapped_column(_enum(SiteStatus), default=SiteStatus.RUMORED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    signals: Mapped[list[Signal]] = relationship(back_populates="site")
    equipment: Mapped[list[Equipment]] = relationship(back_populates="site")
    status_changes: Mapped[list[SiteStatusChange]] = relationship(
        back_populates="site", order_by="SiteStatusChange.changed_at"
    )

    def set_status(
        self,
        new_status: SiteStatus,
        *,
        signal: Signal | None = None,
        when: datetime | None = None,
        note: str | None = None,
    ) -> None:
        """Change status and log it. Use this instead of assigning .status directly."""
        if new_status == self.status and self.status_changes:
            return
        self.status_changes.append(
            SiteStatusChange(
                old_status=self.status if self.status_changes else None,
                new_status=new_status,
                changed_at=when or utcnow(),
                signal=signal,
                note=note,
            )
        )
        self.status = new_status


class SiteStatusChange(Base):
    __tablename__ = "site_status_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"), index=True)
    old_status: Mapped[SiteStatus | None] = mapped_column(_enum(SiteStatus))
    new_status: Mapped[SiteStatus] = mapped_column(_enum(SiteStatus))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signal.id"))
    note: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site] = relationship(back_populates="status_changes")
    signal: Mapped[Signal | None] = relationship()


class Document(Base):
    """A raw file exactly as downloaded. sha256 is unique, so nothing is stored twice."""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    local_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Site name in the listing the document was found through (Records Online search).
    listed_site_name: Mapped[str | None] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    signals: Mapped[list[Signal]] = relationship(back_populates="document")


class Signal(Base):
    __tablename__ = "signal"
    __table_args__ = (UniqueConstraint("document_id", "type", name="uq_signal_document_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("site.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id"))
    type: Mapped[SignalType] = mapped_column(_enum(SignalType))
    source: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    filing_date: Mapped[date | None] = mapped_column(Date)
    permit_number: Mapped[str | None] = mapped_column(String(64), index=True)
    rn: Mapped[str | None] = mapped_column(String(16), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    site: Mapped[Site | None] = relationship(back_populates="signals")
    document: Mapped[Document | None] = relationship(back_populates="signals")
    extractions: Mapped[list[Extraction]] = relationship(back_populates="signal")
    equipment: Mapped[list[Equipment]] = relationship(back_populates="signal")


class Extraction(Base):
    """Structured fields pulled from a signal by one extractor version. Re-runnable."""

    __tablename__ = "extraction"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signal.id"), index=True)
    extractor: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(32))
    fields: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    signal: Mapped[Signal] = relationship(back_populates="extractions")
    equipment: Mapped[list[Equipment]] = relationship(back_populates="extraction")


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signal.id"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("site.id"), index=True)
    extraction_id: Mapped[int | None] = mapped_column(ForeignKey("extraction.id"))
    category: Mapped[EquipmentCategory] = mapped_column(_enum(EquipmentCategory))
    count: Mapped[int] = mapped_column(Integer)
    unit_size: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))  # kW, MW, hp, gal, bbl
    fuel: Mapped[str | None] = mapped_column(String(32))
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    page: Mapped[int | None] = mapped_column(Integer)  # 1-based page in the source document
    snippet: Mapped[str | None] = mapped_column(Text)  # the sentence the numbers came from
    # prose, count_change, or maert (one row per unit in the emission rates table)
    method: Mapped[str | None] = mapped_column(String(32))
    # total, existing, added, removed, or unknown: what the sentence says about these units
    role: Mapped[str | None] = mapped_column(String(16))
    epns: Mapped[str | None] = mapped_column(String(64))  # unit labels, e.g. GEN-1..GEN-3

    signal: Mapped[Signal] = relationship(back_populates="equipment")
    site: Mapped[Site | None] = relationship(back_populates="equipment")
    extraction: Mapped[Extraction | None] = relationship(back_populates="equipment")


class Entity(Base):
    """A legal entity named on a filing (usually an LLC).

    `officers` may hold names of private individuals. It is for internal clustering only
    and must never be exported to the public app or committed to the repo.
    """

    __tablename__ = "entity"

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(300), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    formation_date: Mapped[date | None] = mapped_column(Date)
    registered_agent: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)
    sos_file_number: Mapped[str | None] = mapped_column(String(32), index=True)
    taxpayer_number: Mapped[str | None] = mapped_column(String(16), index=True)
    officers: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Company(Base):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    role: Mapped[CompanyRole] = mapped_column(_enum(CompanyRole))
    is_public: Mapped[bool] = mapped_column(default=False)

    tickers: Mapped[list[Ticker]] = relationship(back_populates="company")


class Ticker(Base):
    __tablename__ = "ticker"
    __table_args__ = (UniqueConstraint("symbol", "exchange", name="uq_ticker_symbol_exchange"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(16))

    company: Mapped[Company] = relationship(back_populates="tickers")


class Link(Base):
    """A typed edge between any two records, always with method, confidence, and evidence.

    Links are append-only: a better guess or a review decision adds a new Link.
    """

    __tablename__ = "link"
    __table_args__ = (
        CheckConstraint(
            "evidence_url IS NOT NULL OR note IS NOT NULL", name="ck_link_has_evidence"
        ),
        Index("ix_link_from", "from_type", "from_id"),
        Index("ix_link_to", "to_type", "to_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_type: Mapped[RecordType] = mapped_column(_enum(RecordType))
    from_id: Mapped[int] = mapped_column(Integer)
    to_type: Mapped[RecordType] = mapped_column(_enum(RecordType))
    to_id: Mapped[int] = mapped_column(Integer)
    link_type: Mapped[LinkType] = mapped_column(_enum(LinkType))
    method: Mapped[LinkMethod] = mapped_column(_enum(LinkMethod))
    confidence: Mapped[Confidence] = mapped_column(_enum(Confidence))
    evidence_url: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
