"""Companies behind the legal entities named in filings, with their tickers.

`exact` lists legal names the company itself files under, so a filing that names one names
the company directly (high confidence). `pattern` catches subsidiaries and project LLCs
that carry the brand ("Vantage Data Centers TX11, LLC"), which is a strong clue but not
proof of who owns the entity today (medium confidence).

Ticker is the parent's primary listing; private companies have none. Keep this list to
companies seen in Texas filings, and check a ticker before adding it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tracker.db.models import CompanyRole


@dataclass(frozen=True)
class KnownCompany:
    name: str
    role: CompanyRole
    ticker: str | None
    exchange: str | None
    pattern: re.Pattern[str]
    exact: tuple[str, ...] = ()


def _p(regex: str) -> re.Pattern[str]:
    return re.compile(regex, re.IGNORECASE)


H, N, R, D, S, OTHER = (
    CompanyRole.HYPERSCALER,
    CompanyRole.NEOCLOUD,
    CompanyRole.REIT,
    CompanyRole.DEVELOPER,
    CompanyRole.SUPPLIER,
    CompanyRole.OTHER,
)

COMPANIES: tuple[KnownCompany, ...] = (
    KnownCompany("Amazon", H, "AMZN", "NASDAQ", _p(r"\bAmazon\b"), ("Amazon Data Services, Inc.",)),
    KnownCompany("Alphabet (Google)", H, "GOOGL", "NASDAQ", _p(r"\bGoogle\b"), ("Google LLC",)),
    KnownCompany(
        "Microsoft", H, "MSFT", "NASDAQ", _p(r"\bMicrosoft\b"), ("Microsoft Corporation",)
    ),
    KnownCompany(
        "Meta",
        H,
        "META",
        "NASDAQ",
        _p(r"\b(?:Meta\s+Platforms|Facebook)\b"),
        ("Meta Platforms, Inc.",),
    ),
    KnownCompany(
        "Oracle",
        H,
        "ORCL",
        "NYSE",
        _p(r"\bOracle\b"),
        ("Oracle America, Inc.", "Oracle America Cloud Services, LLC"),
    ),
    KnownCompany(
        "Apple", H, "AAPL", "NASDAQ", _p(r"\bApple\s+(?:Inc|Operations)\b"), ("Apple Inc.",)
    ),
    KnownCompany("CoreWeave", N, "CRWV", "NASDAQ", _p(r"\bCore\s?weave\b"), ("CoreWeave, Inc.",)),
    KnownCompany(
        "Core Scientific",
        N,
        "CORZ",
        "NASDAQ",
        _p(r"\bCore\s+Scientific\b"),
        ("Core Scientific, Inc.",),
    ),
    KnownCompany("Riot Platforms", N, "RIOT", "NASDAQ", _p(r"\bRiot\b")),
    KnownCompany("Cipher Mining", N, "CIFR", "NASDAQ", _p(r"\bCipher\b")),
    KnownCompany("Applied Digital", N, "APLD", "NASDAQ", _p(r"\bApplied\s+Digital\b")),
    KnownCompany("Galaxy Digital", N, "GLXY", "NASDAQ", _p(r"\bGalaxy\b")),
    KnownCompany("Digital Realty", R, "DLR", "NYSE", _p(r"\bDigital\s+Realty\b")),
    KnownCompany("Equinix", R, "EQIX", "NASDAQ", _p(r"\bEquinix\b")),
    KnownCompany("Iron Mountain", R, "IRM", "NYSE", _p(r"\bIron\s+Mountain\b")),
    KnownCompany("COPT Defense Properties", R, "CDP", "NYSE", _p(r"\bCOPT\b")),
    KnownCompany(
        "NTT", D, "9432", "TSE", _p(r"\bNTT\b"), ("NTT Global Data Centers Americas, Inc.",)
    ),
    # Enterprises that build their own data centers.
    KnownCompany(
        "Bank of America",
        OTHER,
        "BAC",
        "NYSE",
        _p(r"\bBank\s+of\s+America\b"),
        ("Bank of America, National Association", "Bank of America Corporation"),
    ),
    # Private developers and AI labs: no ticker, but grouping their sites still helps.
    KnownCompany("Vantage Data Centers", D, None, None, _p(r"\bVantage\s+Data\s+Centers\b")),
    KnownCompany("QTS", D, None, None, _p(r"\bQTS\b|\bQuality\s+Technology\s+Services\b")),
    KnownCompany("CyrusOne", D, None, None, _p(r"\bCyrus\s?One\b")),
    KnownCompany("Compass Datacenters", D, None, None, _p(r"\bCompass\s+Datacenters\b")),
    KnownCompany("Stream Data Centers", D, None, None, _p(r"\bStream\s+Data\s+Centers\b|\bSDC\b")),
    KnownCompany("DataBank", D, None, None, _p(r"\bData\s?bank\b|\bDB\s+Data\s+Center\b")),
    KnownCompany("Lancium", D, None, None, _p(r"\bLancium\b")),
    KnownCompany("Crusoe", D, None, None, _p(r"\bCrusoe\b"), ("Crusoe Energy Systems LLC",)),
    KnownCompany(
        "Nexus Data Centers", D, None, None, _p(r"\bNexus\s+(?:Data|Hubbard|Operations)\b")
    ),
    # On-site power: gas generator microgrids that run as the site's power plant.
    KnownCompany("Enchanted Rock", S, None, None, _p(r"\bEnchanted\s+Rock\b")),
    KnownCompany("Poolside", OTHER, None, None, _p(r"\bPoolside\b")),
    KnownCompany("Anthropic", OTHER, None, None, _p(r"\bAnthropic\b"), ("Anthropic, PBC",)),
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def find_company(legal_name: str) -> tuple[KnownCompany, bool] | None:
    """The company behind a legal name, and whether the name is one of its exact names."""
    norm = _norm(legal_name)
    for company in COMPANIES:
        if any(_norm(e) == norm for e in company.exact):
            return company, True
    for company in COMPANIES:
        if company.pattern.search(legal_name):
            return company, False
    return None
