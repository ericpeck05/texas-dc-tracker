"""SEC EDGAR: public company filings that name a tracked site.

EDGAR full-text search (efts.sec.gov) is the SEC's own search API. Each distinctive site
name is searched as an exact phrase in 8-K, 10-K, 10-Q, and S-1 filings since SINCE. A hit
counts only after the document itself is read and it names the site and Texas (or the
site's county or city), with the sentence kept as evidence.

Names are distinctive when they have two or more words, one of them a word of five or more
letters that is not generic ("Longhorn Data Center", not "Data Center 1"), and are not a
company's own name (every NTT filing names NTT Global Data Centers).

The SEC asks automated clients to name a contact in the User-Agent: set
TRACKER_SEC_CONTACT. It is sent only to sec.gov.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, replace
from urllib.parse import quote

from tracker.config import Settings
from tracker.locate import _norm, _table
from tracker.reference.companies import find_company

SOURCE = "sec_edgar"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
PAGE_URL = "https://efts.sec.gov/LATEST/search-index"
FORMS = "8-K,10-K,10-Q,S-1"
SINCE = "2023-01-01"
MAX_HITS = 10  # per name; a name with more is too common to be one site
SNIPPET_CHARS = 240
_GENERIC = {
    "data",
    "center",
    "centers",
    "datacenter",
    "campus",
    "project",
    "building",
    "buildings",
    "phase",
    "texas",
    "county",
    "north",
    "south",
    "east",
    "west",
    "earthwork",
    "special",
    "private",
    "shell",
}


_DATA_CENTER_RE = re.compile(r"\bdata\s*cent(?:er|re)s?\b|\bdatacent|\bDC\b", re.I)


def _is_place(word: str) -> bool:
    w = _norm(word)
    return w in _table("tx_places.csv") or w in _table("tx_counties.csv")


class MissingContact(RuntimeError):
    pass


def sec_settings(settings: Settings) -> Settings:
    if not settings.sec_contact:
        raise MissingContact("set TRACKER_SEC_CONTACT in .env (the SEC asks for a contact)")
    return replace(settings, user_agent=f"texas-dc-tracker/0.1 {settings.sec_contact}")


def search_phrase(name: str) -> str | None:
    """The exact phrase to search for a site name, or None if it is not distinctive."""
    name = re.sub(r"\s*\([^)]*\)", "", name).strip(" -")
    words = re.findall(r"[A-Za-z]+", name)
    if len(name.split()) < 2 or re.search(r"\b(?:LLC|Inc|L\.?P\.?|Corp)\b", name, re.I):
        return None
    strong = [w for w in words if len(w) >= 5 and w.lower() not in _GENERIC]
    if not strong:
        return None
    # "Plano Campus" could be any company's office; "Pyote Data Center" is a project.
    if all(_is_place(w) for w in strong) and not _DATA_CENTER_RE.search(name):
        return None
    if find_company(name):
        return None
    return name


def search_url(phrase: str, today: str) -> str:
    q = quote(f'"{phrase}"')
    return f"{SEARCH_URL}?q={q}&forms={quote(FORMS)}&dateRange=custom&startdt={SINCE}&enddt={today}"


def document_url(cik: str, adsh: str, filename: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{filename}"


def text_of(document: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", document)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def evidence(text: str, phrase: str, places: list[str]) -> str | None:
    """The sentence around the phrase, if the document also names Texas or the site's place."""
    m = re.search(re.escape(phrase), text, re.I)
    if not m:
        return None
    if not any(re.search(rf"\b{re.escape(p)}\b", text, re.I) for p in ["Texas", *places] if p):
        return None
    start = max(0, m.start() - SNIPPET_CHARS)
    end = min(len(text), m.end() + SNIPPET_CHARS)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


@dataclass
class Query:
    phrase: str
    places: list[str]  # the site's county and city, for verification


@dataclass
class Snapshot:
    urls: list[str]
    hits: list[dict]  # phrase, adsh, cik, company, form, file_date, file_type, url, snippet

    def to_bytes(self) -> bytes:
        return json.dumps({"urls": self.urls, "hits": self.hits}, sort_keys=True).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> Snapshot:
        data = json.loads(content)
        return cls(data["urls"], data["hits"])


def _company(display_names: list[str]) -> tuple[str, str | None]:
    """'NUSCALE POWER Corp  (SMR, SMR-WT)  (CIK 0001822966)' -> ('NUSCALE POWER Corp', 'SMR')."""
    first = display_names[0] if display_names else ""
    name = re.sub(r"\s*\(.*$", "", first).strip()
    tick = re.search(r"\(([A-Z.\-]+)(?:,[^)]*)?\)\s*\(CIK", first)
    return name, tick.group(1) if tick else None


def fetch(client, queries: list[Query], today: str) -> Snapshot:
    urls, hits = [], []
    for q in queries:
        url = search_url(q.phrase, today)
        urls.append(url)
        data = client.get(url).json()
        found = data.get("hits", {}).get("hits", [])
        if not found or data["hits"]["total"]["value"] > MAX_HITS:
            continue
        for h in found:
            src = h["_source"]
            adsh, filename = h["_id"].split(":", 1)
            cik = (src.get("ciks") or ["0"])[0]
            doc_url = document_url(cik, adsh, filename)
            urls.append(doc_url)
            snippet = evidence(text_of(client.get(doc_url).text), q.phrase, q.places)
            if snippet is None:
                continue
            company, ticker = _company(src.get("display_names") or [])
            hits.append(
                {
                    "phrase": q.phrase,
                    "adsh": adsh,
                    "cik": cik,
                    "company": company,
                    "ticker": ticker,
                    "form": src.get("form"),
                    "file_type": src.get("file_type"),
                    "file_date": src.get("file_date"),
                    "url": doc_url,
                    "snippet": snippet,
                }
            )
    return Snapshot(urls, hits)
