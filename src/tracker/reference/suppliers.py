"""Equipment manufacturers seen in air permits, with how to recognize them and their ticker.

Ticker is the parent's primary listing. Private makers have ticker None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Supplier:
    name: str
    pattern: re.Pattern[str]
    ticker: str | None
    exchange: str | None
    parent: str | None = None


def _ci(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


# Order matters: more specific patterns first (e.g. "GE Vernova" before "GE").
SUPPLIERS: tuple[Supplier, ...] = (
    Supplier(
        "Solar Turbines",
        _ci(r"\bSolar\s+(?:Turbines\b|(?=Titan|Taurus|Mars|Centaur|Mercury))"),
        "CAT",
        "NYSE",
        parent="Caterpillar",
    ),
    Supplier("Perkins", _ci(r"\bPerkins\b"), "CAT", "NYSE", parent="Caterpillar"),
    Supplier("Caterpillar", re.compile(r"\b(?:Caterpillar|CATERPILLAR|Cat®|CAT)\b"), "CAT", "NYSE"),
    Supplier("Cummins", _ci(r"\bCummins\b"), "CMI", "NYSE"),
    Supplier(
        "GE Vernova", re.compile(r"\b(?:GE\s+Vernova|General\s+Electric|GE)\b"), "GEV", "NYSE"
    ),
    Supplier("Generac", _ci(r"\bGenerac\b"), "GNRC", "NYSE"),
    Supplier("Rolls-Royce (mtu)", re.compile(r"\b(?:Rolls[- ]Royce|MTU|mtu)\b"), "RR.", "LSE"),
    Supplier("Kohler / Rehlko", _ci(r"\b(?:Kohler|Rehlko)\b"), None, None),
    Supplier("Siemens Energy", _ci(r"\bSiemens(?:\s+Energy)?\b"), "ENR", "XETRA"),
    Supplier(
        "Mitsubishi Heavy Industries",
        _ci(r"\bMitsubishi(?:\s+(?:Power|Heavy\s+Industries))?\b"),
        "7011",
        "TSE",
    ),
    Supplier("Wärtsilä", _ci(r"\bW[äa]rtsil[äa]\b"), "WRT1V", "HEL"),
    Supplier("INNIO Jenbacher", _ci(r"\b(?:Jenbacher|INNIO)\b"), None, None),
    Supplier("Baker Hughes", _ci(r"\bBaker\s+Hughes\b"), "BKR", "NASDAQ"),
    Supplier(
        "MAN Energy Solutions",
        re.compile(r"\bMAN(?:\s+Energy\s+Solutions)?\b"),
        None,
        None,
        parent="Volkswagen",
    ),
    Supplier("Kawasaki", _ci(r"\bKawasaki\b"), "7012", "TSE"),
)


def find_supplier(text: str) -> tuple[Supplier, re.Match[str]] | None:
    """Return the earliest-occurring supplier mention in text."""
    best: tuple[Supplier, re.Match[str]] | None = None
    for supplier in SUPPLIERS:
        m = supplier.pattern.search(text)
        if m and (best is None or m.start() < best[1].start()):
            best = (supplier, m)
    return best
