"""Rule-based extraction of equipment from TCEQ air permit text.

Finds phrases like:
    "eighteen (18) 2,500-kW diesel-fired emergency generators"
    "Thirty-two (32) Caterpillar 3516C diesel engines, each rated at 2,682 bhp"
    "five (5) GE LM6000PF natural gas-fired combustion turbines"

Each equipment noun (generator, engine, turbine, tank, generating unit) is an anchor.
The count is the closest explicit number before it; size, fuel, make, and model come from
the words between the count and the noun plus a short window after the noun.
Anything without an explicit count is skipped rather than guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from tracker.db.models import EquipmentCategory
from tracker.reference.suppliers import find_supplier

EXTRACTOR_NAME = "tceq_air_rules"
EXTRACTOR_VERSION = "0.1.0"

LOOKBACK_CHARS = 160
LOOKAHEAD_CHARS = 110

_UNITS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_UNIT_WORDS = "|".join(sorted(_UNITS, key=len, reverse=True))
_TENS_WORDS = "|".join(_TENS)
_WORD_NUMBER = rf"(?:(?:{_TENS_WORDS})(?:[- ](?:{_UNIT_WORDS}))?|(?:{_UNIT_WORDS})|one\s+hundred)"

COUNT_RE = re.compile(
    rf"\((?P<paren>\d{{1,4}})\)"
    rf"|\b(?P<word>{_WORD_NUMBER})\b"
    # bare integers: not part of a decimal/rule number, not a size, not after "Rule"/"No."
    r"|(?<![\d.,§#])(?<!Rule )(?<!No\. )(?<!EPN )(?<!Unit )\b(?P<bare>\d{1,3})\b(?![\d.,])"
    r"(?!\s*-?\s*(?:kWe?|MWe?|b?hp|horsepower|gal|gallons?|%|percent|ppm|tons?|tpy|hours?|hrs?"
    r"|lbs?|feet|ft|miles?|acres?)\b)",
    re.IGNORECASE,
)

NOUN_RE = re.compile(
    r"\b(?P<noun>generator\s+sets?|gensets?|generators?|generating\s+units?|engines?"
    r"|turbines?|storage\s+tanks?|tanks?)\b",
    re.IGNORECASE,
)

POWER_SIZE_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*-?\s*"
    r"(?P<unit>kWe?|MWe?|bhp|hp|horsepower)\b",
    re.IGNORECASE,
)
TANK_SIZE_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*-?\s*(?P<unit>gal(?:lons?)?)\b",
    re.IGNORECASE,
)
MODEL_RE = re.compile(
    r"\s*(?:Model\s+)?(?P<model>(?:[A-Z][a-z]+\s)?[A-Z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)"
)

DIESEL_RE = re.compile(r"\b(?:diesel|ULSD|distillate|fuel\s+oil)\b", re.IGNORECASE)
GAS_RE = re.compile(r"\b(?:natural[\s-]+gas|gas[\s-]+fired|pipeline[\s-]+quality)\b", re.IGNORECASE)
DUAL_RE = re.compile(r"\bdual[\s-]+fuel\b", re.IGNORECASE)
FIRE_PUMP_RE = re.compile(r"\bfire[\s-]+(?:water\s+)?pump\b", re.IGNORECASE)

RN_RE = re.compile(r"\bRN\s?-?(\d{9})\b")
CN_RE = re.compile(r"\bCN\s?-?(\d{9})\b")
PERMIT_RE = re.compile(
    r"\b(?:Air\s+Quality\s+)?(?:Standard\s+)?Permit\s+(?:No\.?|Number)\s*:?\s*([A-Z]{0,4}\d[\dA-Z\-]*)",
    re.IGNORECASE,
)
COUNTY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+County\b")
APPLICANT_RE = re.compile(
    r"^\s*(?:Applicant|Permittee|Company\s+Name)\s*:\s*(.+?)\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class EquipmentMention:
    category: EquipmentCategory
    count: int
    unit_size: float | None
    unit: str | None
    fuel: str | None
    manufacturer: str | None
    model: str | None
    ticker: str | None
    page: int
    snippet: str

    def key(self) -> tuple:
        return (self.category, self.count, self.unit_size, self.unit, self.manufacturer, self.model)


@dataclass
class AirPermitExtraction:
    rn: str | None = None
    cn: str | None = None
    permit_number: str | None = None
    county: str | None = None
    applicant: str | None = None
    equipment: list[EquipmentMention] = field(default_factory=list)
    duplicate_mentions: int = 0

    def to_fields(self) -> dict[str, Any]:
        data = asdict(self)
        for item in data["equipment"]:
            item["category"] = str(item["category"])
        return data


def _word_to_int(word: str) -> int:
    w = word.lower().replace("-", " ").split()
    if w == ["one", "hundred"]:
        return 100
    total = 0
    for part in w:
        total += _TENS.get(part, 0) + _UNITS.get(part, 0)
    return total


def _count_value(m: re.Match[str]) -> int:
    if m.group("paren"):
        return int(m.group("paren"))
    if m.group("word"):
        return _word_to_int(m.group("word"))
    return int(m.group("bare"))


def _normalize_unit(unit: str) -> str:
    u = unit.lower()
    if u.startswith("kw"):
        return "kW"
    if u.startswith("mw"):
        return "MW"
    if u.startswith("gal"):
        return "gal"
    return "hp"


def _fuel(text: str) -> str | None:
    if DUAL_RE.search(text):
        return "dual"
    has_diesel = bool(DIESEL_RE.search(text))
    has_gas = bool(GAS_RE.search(text))
    if has_diesel and has_gas:
        return "dual"
    if has_diesel:
        return "diesel"
    if has_gas:
        return "natural_gas"
    return None


def _category(noun: str, fuel: str | None, context: str) -> EquipmentCategory:
    n = noun.lower()
    if "tank" in n:
        return EquipmentCategory.FUEL_TANK
    if "turbine" in n:
        return EquipmentCategory.GAS_TURBINE
    if FIRE_PUMP_RE.search(context):
        return EquipmentCategory.OTHER
    if fuel == "diesel":
        return EquipmentCategory.DIESEL_GENERATOR
    if fuel == "natural_gas":
        return EquipmentCategory.GAS_GENERATOR
    return EquipmentCategory.GENERATOR


def _snippet(text: str, start: int, end: int) -> str:
    stop = text.rfind(". ", 0, start)
    left = max(stop + 2 if stop != -1 else 0, start - 200)
    right_candidates = [i for i in (text.find(". ", end),) if i != -1]
    right = min(right_candidates[0] + 1 if right_candidates else len(text), end + 200)
    return text[left:right].strip()


@dataclass(frozen=True)
class _Noun:
    start: int
    end: int
    text: str


def _noun_groups(text: str) -> list[_Noun]:
    """Merge adjacent nouns ("turbine generators", "generator engines") into one anchor."""
    groups: list[_Noun] = []
    for m in NOUN_RE.finditer(text):
        if groups and not text[groups[-1].end : m.start()].strip(" -"):
            last = groups[-1]
            groups[-1] = _Noun(last.start, m.end(), f"{last.text} {m.group('noun')}")
        else:
            groups.append(_Noun(m.start(), m.end(), m.group("noun")))
    return groups


def _scan_page(text: str, page_no: int) -> list[EquipmentMention]:
    mentions: list[EquipmentMention] = []
    nouns = _noun_groups(text)
    for i, noun in enumerate(nouns):
        prev_end = nouns[i - 1].end if i > 0 else 0
        next_start = nouns[i + 1].start if i + 1 < len(nouns) else len(text)

        back_start = max(prev_end, noun.start - LOOKBACK_CHARS)
        back = text[back_start : noun.start]
        counts = list(COUNT_RE.finditer(back))
        if not counts:
            continue
        # Prefer explicit counts ("five (5)") over bare integers, which may be model numbers.
        explicit = [c for c in counts if c.group("paren") or c.group("word")]
        count_m = (explicit or counts)[-1]
        count = _count_value(count_m)
        if count <= 0:
            continue

        ahead = text[noun.end : min(next_start, noun.end + LOOKAHEAD_CHARS)]
        next_count = COUNT_RE.search(ahead)
        if next_count:
            ahead = ahead[: next_count.start()]

        descriptor = back[count_m.end() :]
        noun_text = noun.text
        context = f"{descriptor} {noun_text} {ahead}"

        fuel = _fuel(context)
        category = _category(noun_text, fuel, context)

        size_re = TANK_SIZE_RE if category is EquipmentCategory.FUEL_TANK else POWER_SIZE_RE
        size_m = size_re.search(context)
        unit_size = float(size_m.group("num").replace(",", "")) if size_m else None
        unit = _normalize_unit(size_m.group("unit")) if size_m else None

        manufacturer = model = ticker = None
        found = find_supplier(context)
        if found:
            supplier, sm = found
            manufacturer, ticker = supplier.name, supplier.ticker
            mm = MODEL_RE.match(context, sm.end())
            if mm:
                model = mm.group("model").removeprefix("Model ").strip()

        mentions.append(
            EquipmentMention(
                category=category,
                count=count,
                unit_size=unit_size,
                unit=unit,
                fuel=fuel,
                manufacturer=manufacturer,
                model=model,
                ticker=ticker,
                page=page_no,
                snippet=_snippet(text, back_start + count_m.start(), noun.end),
            )
        )
    return mentions


def extract_air_permit(text: str) -> AirPermitExtraction:
    result = AirPermitExtraction()

    if m := RN_RE.search(text):
        result.rn = f"RN{m.group(1)}"
    if m := CN_RE.search(text):
        result.cn = f"CN{m.group(1)}"
    if m := PERMIT_RE.search(text):
        result.permit_number = m.group(1).rstrip("-")
    counties = Counter(m.group(1) for m in COUNTY_RE.finditer(text))
    if counties:
        result.county = counties.most_common(1)[0][0]
    if m := APPLICANT_RE.search(text):
        result.applicant = m.group(1)

    seen: set[tuple] = set()
    for page_no, page in enumerate(text.split("\f"), start=1):
        flat = " ".join(page.split())
        for mention in _scan_page(flat, page_no):
            if mention.key() in seen:
                result.duplicate_mentions += 1
                continue
            seen.add(mention.key())
            result.equipment.append(mention)
    return result
