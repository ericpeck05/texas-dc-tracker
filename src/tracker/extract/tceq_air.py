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
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

from tracker.db.models import EquipmentCategory
from tracker.reference.suppliers import find_supplier

EXTRACTOR_NAME = "tceq_air_rules"
EXTRACTOR_VERSION = "0.4.0"

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

# Words that turn a number into something other than a unit count: "ten days",
# "one for each generator", "60 minutes".
_NOT_A_COUNT_AFTER = (
    r"days?|weeks?|months?|years?|minutes?|seconds?|hours?|hrs?|times|percent|for\s+each|per"
    r"|or\s+more"
)

COUNT_RE = re.compile(
    # "(7)" counts only after a written number ("seven (7)"); see _count_fits.
    rf"\((?P<paren>\d{{1,4}})\)"
    rf"|\b(?P<word>{_WORD_NUMBER})\b(?!\s+(?:{_NOT_A_COUNT_AFTER})\b)"
    # "Add a 40,000-barrel floating roof diesel tank": one unit, but only after a build verb.
    r"|\b(?:add|install|construct)\s+(?P<one>an?)\b"
    # Bare integers. Not part of an identifier, decimal, or rule number ("60KKKK-01",
    # "106.511"), not a page/part/tier/chapter number or unit label ("Gen 224"), and not
    # followed by a unit or a regulation name ("850 MMBtu/hr", "75%", "30 TAC", "40 CFR",
    # "Page 11 of 23").
    r"|(?<![\w.,§#/\-])(?<!Rule )(?<!No\. )(?<!EPN )(?<!Unit )(?<!Page )(?<!Part )"
    r"(?<!Subpart )(?<!Tier )(?<!Chapter )(?<!Section )(?<!§ )(?<!Index )(?<!\d of )"
    r"(?<!Gen )(?<!Engine )(?<!Turbine )(?<!Generator )(?<!Building )(?<!Phase )"
    r"\b(?P<bare>\d{1,3})\b(?![\d.,])"
    r"(?!\s*-?\s*(?:kWe?|MWe?|b?hp|horsepower|gal|gallons?|percent|ppm|tons?|tpy"
    r"|lbs?|feet|ft|miles?|acres?|MMBtu|Btu|CFR|TAC|U\.?S\.?C|degrees?|psia?|liters?"
    rf"|a?cfm|cubic|of\s+\d|{_NOT_A_COUNT_AFTER})\b)"
    r"(?!\s*(?:%|°))",
    re.IGNORECASE,
)
_WRITTEN_NUMBER_END_RE = re.compile(rf"\b{_WORD_NUMBER}\s*$", re.IGNORECASE)

# A count never reaches across a sentence end, a semicolon, a colon, or a Title V
# "Attribute = value" separator. Without this, "75% of Peak = The combustion turbine" reads
# as 75 turbines, and "255 FILLMORE ST ... Re: Electric Generating Units" as 255 generators.
CLAUSE_BREAK_RE = re.compile(r"[.!?](?=\s+[A-Z(])|[;=:]")
# A number right after a model family or "Model" is part of the model name: "Titan 350".
MODEL_PREFIX_RE = re.compile(
    r"\b(?:Titan|Taurus|Mars|Centaur|Mercury|Model|Series|LM|QSK|Type)\s*$", re.IGNORECASE
)
# Real equipment phrases keep the count close to the noun:
# "Thirty-two (32) Caterpillar 3516C diesel emergency generator engines" is 5 words apart.
MAX_WORDS_BETWEEN = 8
# A bare integer ("24 Cummins QSK95 generators") is trusted only when the words up to the
# noun describe equipment. Filler words mean it's a different number, as in
# "Tier 1, 2, 3, or 4 standards applicable to non-emergency engines".
BARE_COUNT_STOPWORDS = frozenset(
    {"to", "than", "the", "is", "are", "of", "for", "with", "by", "or", "standards", "standard"}
)


def _count_fits(back: str, m: re.Match[str]) -> bool:
    words = back[m.end() :].split()
    if len(words) > MAX_WORDS_BETWEEN:
        return False
    if m.group("bare") or m.group("one"):
        if any(w.lower().strip(",") in BARE_COUNT_STOPWORDS for w in words):
            return False
    if m.group("bare") and MODEL_PREFIX_RE.search(back[: m.start()]):
        return False
    # A lone "(3)" is a rule paragraph number: "(3) The capacity of any tank ...".
    if m.group("paren") and not _WRITTEN_NUMBER_END_RE.search(back[: m.start()]):
        return False
    return True


NOUN_RE = re.compile(
    # Not the tail of an identifier like GRP-TANKS or GRP-GEN1.
    r"(?<!(?-i:[A-Z0-9])-)"
    r"\b(?P<noun>generator\s+sets?|gensets?|generators?|generating\s+units?|engines?"
    r"|turbines?|storage\s+tanks?|tanks?)\b",
    re.IGNORECASE,
)

POWER_SIZE_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*-?\s*"
    # "3-megawatt (MW)" spells the unit out and repeats it in parentheses.
    r"(?P<unit>kWe?|MWe?|megawatts?|kilowatts?|bhp|hp|horsepower)\b(?:\s*\((?:MW|kW)\))?",
    re.IGNORECASE,
)
TANK_SIZE_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*-?\s*"
    r"(?P<unit>gal(?:lons?)?|barrels?|bbls?)\b",
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
# Spaces only (no newlines), so "Nearest City: Abilene\nCounty: Taylor" can't read as
# "Abilene County".
COUNTY_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) County\b")
APPLICANT_RE = re.compile(
    r"^\s*(?:Applicant|Permittee|Company\s+Name)\s*:\s*(.+?)\s*$", re.MULTILINE
)


# Field labels at the start of a line. The colon is optional because NSR technical reviews
# lay fields out as a table: "County Taylor Regulated Entity Number RN112029079". A value
# ends at the end of the line or where the next known label starts.
_NEXT_LABEL = r"(?:Regulated|Project|Customer|Registration|Permit|City|County)\b"


def _label_re(label: str, value: str = r".+?") -> re.Pattern[str]:
    return re.compile(
        rf"^[ \t]*{label}[ \t]*:?[ \t]+({value})(?=[ \t]+{_NEXT_LABEL}|[ \t]*$)", re.MULTILINE
    )


COUNTY_LABEL_RE = _label_re("County", r"[A-Z][a-z]+(?: [A-Z][a-z]+)?")
# PBR reviews call the site "Unit Name"; PBR letters say "Project Description/Unit".
SITE_NAME_LABEL_RE = _label_re(r"(?:Site Name|Unit Name|Project Description/Unit)")
NEAREST_CITY_LABEL_RE = _label_re("Nearest City")
COMPANY_LABEL_RE = _label_re("Company")
PERMIT_LABEL_RE = _label_re(r"Permit (?:Number|No\.?)", r"[A-Z]{0,4}\d[\dA-Z\-]*")
REGISTRATION_RE = re.compile(r"\bRegistration (?:Number|No\.?)\s*:?\s*(\d{4,7})\b")

TITLE_V_SOB_RE = re.compile(r"Statement of Basis of the Federal Operating Permit[ \t]*\n(.+)\n")
TECH_REVIEW_RE = re.compile(r"^[ \t]*Technical Review[ \t]*$", re.MULTILINE)
PBR_HEADING_RE = re.compile(r"Permit by Rule \(PBR\) Registration")
REGISTRATION_LETTER_RE = re.compile(r"Standard Permit Registration Number:")
PBR_LETTER_RE = re.compile(r"Permit by Rule Registration Number:")
WITHDRAWN_RE = re.compile(
    r"Project Type\s+[\w/ ]*Withdraw|Re:\s*Permit Application Withdrawal"
    r"|request(?:ed)?\s+(?:that\s+this\s+project\s+is|to)\s+withdraw",
    re.IGNORECASE,
)
# "PBR No(s).: Revised: 106.472, 106.511" and letter lines "30 TAC § 106.511".
PBR_LIST_RE = re.compile(r"^[ \t]*(?:PBR No\(s\)\.?:|30 TAC §)[^\n]*", re.MULTILINE)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(
    rf"\b(?P<month>{_MONTHS}) (?P<day>\d{{1,2}}), (?P<year>\d{{4}})\b"
    r"|\b(?P<m>\d{2})/(?P<d>\d{2})/(?P<y>\d{4})\b"
)
MAERT_HEADING_RE = re.compile(r"MAXIMUM ALLOWABLE EMISSION RATES TABLE|Emission Rates Table")
# One MAERT row per emission point: "Turbine 1 / Titan 350 simple-cycle 5.24 22.96 ...".
# Only EPNs that end in a unit number count, so "FUG / Fugitives" is not a unit.
MAERT_ROW_RE = re.compile(
    r"^[ \t]*(?P<epn>[A-Za-z][A-Za-z\-]*[ \-]?\d{1,3}[A-Za-z]?)[ \t]*/[ \t]*"
    r"(?P<src>[A-Za-z].*?)(?=[ \t]+<?\d+\.\d+|[ \t]*$)",
    re.MULTILINE,
)
# Modification wording: "Reduce the count of Titan 350 simple-cycle turbines from six to five".
COUNT_CHANGE_RE = re.compile(
    rf"\bcount of (?P<desc>.{{0,80}}?)\b(?P<noun>turbines?|generators?|engines?|tanks?)"
    rf"\s+from\s+(?P<old>{_WORD_NUMBER}|\d{{1,3}})\s+to\s+(?P<new>{_WORD_NUMBER}|\d{{1,3}})\b",
    re.IGNORECASE,
)
# Title V unit groups, e.g. GRP-GEN1, GRP-TURB2, GRP-TANKS. Case-sensitive on purpose.
UNIT_GROUP_RE = re.compile(r"\bGRP-[A-Z][A-Z0-9]*\b")
NSR_AUTH_RE = re.compile(
    r"Authorization No\.?:\s*(\d{4,7})\s+Issuance Date:\s*(\d{2})/(\d{2})/(\d{4})"
)
PBR_RE = re.compile(r"\bNumber:\s*(106\.\d{3})\b")
# A filing that says another permit is at the same site, e.g. a PBR review noting
# "The EGU Standard Permit No. 177263 is also located at this site".
SAME_SITE_PERMIT_RE = re.compile(
    r"Permit No\.?\s*(\d{4,7})\s+(?:is|are)\s+also\s+located\s+at\s+this\s+site", re.IGNORECASE
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
    # How the count was found: "prose" (a sentence), "count_change" ("from six to five"),
    # or "maert" (one row per unit in the emission rates table, the most reliable).
    method: str = "prose"
    # What the sentence says about these units: "total" (the full list after this
    # project), "existing", "added", "removed", or "unknown". See rollup for how they net.
    role: str = "unknown"
    # Unit labels when the permit gives them, e.g. "GEN-1 through GEN-3". Two groups with
    # the same make and size but different labels are different units.
    epns: str | None = None

    def key(self) -> tuple:
        return (
            self.category,
            self.count,
            self.unit_size,
            self.unit,
            self.manufacturer,
            self.model,
            self.epns,
        )


@dataclass
class AirPermitExtraction:
    document_kind: str | None = None  # e.g. "title_v_statement_of_basis"
    document_date: str | None = None  # ISO date: latest non-future date in the document
    # The applicant withdrew this application, so nothing in it was authorized.
    withdrawn: bool = False
    rn: str | None = None
    cn: str | None = None
    permit_number: str | None = None
    county: str | None = None
    applicant: str | None = None
    site_name: str | None = None
    nearest_city: str | None = None
    # Title V groups like GRP-GEN1: the permit names them but often not how many units
    # each holds, so they are kept here rather than guessed into Equipment counts.
    unit_groups: list[dict[str, Any]] = field(default_factory=list)
    # NSR permits this document points to. These usually hold the actual unit counts.
    nsr_authorizations: list[dict[str, str]] = field(default_factory=list)
    # Other permits the filing says are at the same site.
    same_site_permits: list[str] = field(default_factory=list)
    permits_by_rule: list[str] = field(default_factory=list)
    equipment: list[EquipmentMention] = field(default_factory=list)
    duplicate_mentions: int = 0
    # Prose counts dropped because the same document's MAERT lists those units one by one.
    superseded_mentions: list[EquipmentMention] = field(default_factory=list)
    # MAERT rows not used because the unit numbers have gaps (rows the parser missed).
    partial_tables: list[EquipmentMention] = field(default_factory=list)

    def to_fields(self) -> dict[str, Any]:
        data = asdict(self)
        for item in [*data["equipment"], *data["superseded_mentions"], *data["partial_tables"]]:
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
    if m.group("one"):
        return 1
    return int(m.group("bare"))


def _normalize_unit(unit: str) -> str:
    u = unit.lower()
    if u.startswith(("kw", "kilowatt")):
        return "kW"
    if u.startswith(("mw", "megawatt")):
        return "MW"
    if u.startswith("gal"):
        return "gal"
    if u.startswith(("barrel", "bbl")):
        return "bbl"
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


def _make_model(text: str) -> tuple[str | None, str | None, str | None]:
    found = find_supplier(text)
    if not found:
        return None, None, None
    supplier, sm = found
    mm = MODEL_RE.match(text, sm.end())
    model = mm.group("model").removeprefix("Model ").strip() if mm else None
    return supplier.name, model, supplier.ticker


def _count_changes(text: str, page_no: int) -> tuple[list[EquipmentMention], list[range]]:
    """ "Reduce the count of Titan 350 simple-cycle turbines from six to five" -> 5 turbines."""
    mentions: list[EquipmentMention] = []
    spans: list[range] = []
    for m in COUNT_CHANGE_RE.finditer(text):
        new = m.group("new")
        count = int(new) if new.isdigit() else _word_to_int(new)
        if count <= 0:
            continue
        # The rest of this change item can state the unit size. An item ends at the next
        # change, a semicolon (list items), or a sentence end.
        nxt = COUNT_CHANGE_RE.search(text, m.end())
        tail = text[m.end() : min(nxt.start() if nxt else len(text), m.end() + 140)]
        if end := re.search(r";|\.(?=\s+[A-Z])", tail):
            tail = tail[: end.start()]
        context = f"{m.group('desc')} {m.group('noun')} {tail}"
        fuel = _fuel(context)
        category = _category(m.group("noun"), fuel, context)
        size_re = TANK_SIZE_RE if category is EquipmentCategory.FUEL_TANK else POWER_SIZE_RE
        size_m = size_re.search(tail)
        manufacturer, model, ticker = _make_model(m.group("desc"))
        mentions.append(
            EquipmentMention(
                category=category,
                count=count,
                unit_size=float(size_m.group("num").replace(",", "")) if size_m else None,
                unit=_normalize_unit(size_m.group("unit")) if size_m else None,
                fuel=fuel,
                manufacturer=manufacturer,
                model=model,
                ticker=ticker,
                page=page_no,
                snippet=text[m.start() : m.end() + len(tail)].strip(" ,"),
                method="count_change",
            )
        )
        spans.append(range(m.start(), m.end() + len(tail)))
    return mentions, spans


# Role cues, nearest to the count wins. "Total" cues describe the whole site after a project.
_ROLE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("removed", re.compile(r"\b(?:remov\w*|decommission\w*|retir\w*)\b", re.I)),
    (
        "total",
        re.compile(
            r"\b(?:total of|site will include|upon authorization|updated equipment list"
            r"|consists of|site includes|following emission sources)\b",
            re.I,
        ),
    ),
    (
        "existing",
        re.compile(r"\b(?:existing|currently|owns and operates|remain\w*)\b", re.I),
    ),
    (
        "added",
        re.compile(
            r"\b(?:add|adding|addition(?:al)?|new|install\w*|construct\w*|propos\w*)\b", re.I
        ),
    ),
)
# Units described as part of a group already counted: "only 5 engines will be operating
# with 2 engines serving as backup", "one existing generator ... will remain".
SUBSET_RE = re.compile(
    r"\s*(?:will\s+be\s+operating|serving\s+as\s+backup|operat\w*\s+(?:at\s+any|simultaneous)"
    r"|(?:\S+\s+){0,12}?will\s+remain\b)",
    re.IGNORECASE,
)
SENTENCE_END_RE = re.compile(r"[.!?](?=\s+[A-Z(])")
# A sentence about an earlier filing: "in February 2024, Vantage submitted a PBR
# registration ... to construct the first 22 diesel generators".
PAST_FILING_RE = re.compile(r"\b(?:submitted|previously|was\s+authorized|the\s+first)\b", re.I)
EPN_RANGE_RE = re.compile(
    r"\(EPNs?\s+([A-Z][\w-]*)\s+(?:through|to|-)\s+([A-Z][\w-]*)\)", re.IGNORECASE
)
# "Remove 32 of the 33 existing generators": the removed count is followed by "of the".
REMOVE_RE = re.compile(
    rf"\bremov\w*\s+(?P<count>\d{{1,3}}|{_WORD_NUMBER})\s+of\s+the\s+(?:\d{{1,3}}\s+)?"
    r"(?:existing\s+)?(?:[\w-]+\s+){0,3}?(?P<noun>generators?|engines?|turbines?|tanks?)\b",
    re.IGNORECASE,
)


def _role(lead: str, descriptor: str) -> str:
    """The cue closest to the count: in the words between count and noun first, then the
    words before the count in the same clause."""
    for role, cue in _ROLE_CUES:
        if cue.search(descriptor):
            return role
    best: tuple[int, str] | None = None
    for role, cue in _ROLE_CUES:
        for m in cue.finditer(lead):
            if best is None or m.end() > best[0]:
                best = (m.end(), role)
    return best[1] if best else "unknown"


def _removals(text: str, page_no: int) -> list[EquipmentMention]:
    out = []
    for m in REMOVE_RE.finditer(text):
        c = m.group("count")
        count = int(c) if c.isdigit() else _word_to_int(c)
        fuel = _fuel(m.group(0))
        out.append(
            EquipmentMention(
                category=_category(m.group("noun"), fuel, m.group(0)),
                count=count,
                unit_size=None,
                unit=None,
                fuel=fuel,
                manufacturer=None,
                model=None,
                ticker=None,
                page=page_no,
                snippet=_snippet(text, m.start(), m.end()),
                role="removed",
            )
        )
    return out


def _paired_counts(back: str, counts: list[re.Match[str]], idx: int) -> list[tuple[int, str]]:
    """Earlier counts joined to this one by a size and "and":
    "six (6) 30.34 MW and six (6) 25.04 MW turbines" -> [(6, "30.34 MW")]."""
    pairs = []
    cur = counts[idx]
    for prev in reversed(counts[:idx]):
        between = back[prev.end() : cur.start()]
        if not between.strip():  # "six (6)" is one count written twice
            cur = prev
            continue
        size = POWER_SIZE_RE.fullmatch(between.strip(" ,").removesuffix(" and").strip(" ,"))
        if size and re.search(r"(?:,|\band)\s*$", between):
            pairs.append((_count_value(prev), size.group(0)))
            cur = prev
            continue
        break
    return pairs


def _scan_page(text: str, page_no: int) -> list[EquipmentMention]:
    mentions, consumed = _count_changes(text, page_no)
    mentions += _removals(text, page_no)
    nouns = _noun_groups(text)
    for i, noun in enumerate(nouns):
        if any(noun.start in span for span in consumed):
            continue
        # "38 MW per turbine", "each engine": a rate, not a count.
        if re.search(r"\b(?:per|each)\s+$", text[max(0, noun.start - 6) : noun.start], re.I):
            continue
        prev_end = nouns[i - 1].end if i > 0 else 0
        next_start = nouns[i + 1].start if i + 1 < len(nouns) else len(text)

        back_start = max(prev_end, noun.start - LOOKBACK_CHARS)
        # Search a little past the noun so "HAPs). Engines" can see the capital after the
        # period, then keep breaks that end before the noun.
        breaks = [
            b
            for b in CLAUSE_BREAK_RE.finditer(text, back_start, noun.start + 2)
            if b.end() <= noun.start
        ]
        if breaks:
            back_start = breaks[-1].end()
        back = text[back_start : noun.start]
        counts = [c for c in COUNT_RE.finditer(back) if _count_fits(back, c)]
        # "add an additional 64 generators": "an" is not the count when a number follows.
        counts = [c for i, c in enumerate(counts) if not (c.group("one") and i < len(counts) - 1)]
        # "32 of the 33 existing generators": the 32 is a part of another group
        # (_removals reads "remove 32 of the ..."), not the count for this noun.
        counts = [c for c in counts if not re.match(r"\s+of\s+the\b", back[c.end() :])]
        if not counts:
            continue
        # Prefer explicit counts ("five (5)") over bare integers, which may be model numbers.
        explicit = [c for c in counts if c.group("paren") or c.group("word") or c.group("one")]
        pool = explicit or counts
        count_m = pool[-1]
        count = _count_value(count_m)
        if count <= 0:
            continue
        # Pairs can sit further from the noun than the word limit, so look at every
        # explicit count in the clause.
        clause_counts = [
            c
            for c in COUNT_RE.finditer(back)
            if (c.group("word") or c.group("paren"))
            and (not c.group("paren") or _WRITTEN_NUMBER_END_RE.search(back[: c.start()]))
        ]
        idx = next((i for i, c in enumerate(clause_counts) if c.start() == count_m.start()), None)
        pairs = _paired_counts(back, clause_counts, idx) if idx is not None else []

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

        manufacturer, model, ticker = _make_model(context)
        epn_m = EPN_RANGE_RE.search(ahead)
        # Role cues can sit before a colon ("Upon authorization, the site will include the
        # following emission sources: ..."), so look back to the sentence start.
        count_pos = back_start + count_m.start()
        sentence_start = max(
            (
                m.end()
                for m in SENTENCE_END_RE.finditer(text, 0, count_pos + 2)
                if m.end() <= count_pos
            ),
            default=0,
        )
        role = _role(text[max(sentence_start, count_pos - 300) : count_pos], descriptor)
        sentence_end = SENTENCE_END_RE.search(text, noun.end)
        rest = text[noun.end : sentence_end.start() if sentence_end else len(text)][:200]
        if SUBSET_RE.match(rest):
            role = "subset"
        snippet = _snippet(text, back_start + count_m.start(), noun.end)

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
                snippet=snippet,
                role=role,
                epns=f"{epn_m.group(1)}..{epn_m.group(2)}" if epn_m else None,
            )
        )
        for pair_count, size_text in pairs:
            size = POWER_SIZE_RE.fullmatch(size_text)
            mentions.append(
                EquipmentMention(
                    category=category,
                    count=pair_count,
                    unit_size=float(size.group("num").replace(",", "")),
                    unit=_normalize_unit(size.group("unit")),
                    fuel=fuel,
                    manufacturer=manufacturer,
                    model=model,
                    ticker=ticker,
                    page=page_no,
                    snippet=snippet,
                    role=role,
                )
            )
    return mentions


_GENERATOR_FAMILY = {
    EquipmentCategory.GENERATOR,
    EquipmentCategory.DIESEL_GENERATOR,
    EquipmentCategory.GAS_GENERATOR,
}


def _same_units(a: EquipmentMention, b: EquipmentMention) -> bool:
    """b restates a: same count and category family, and nothing that disagrees."""
    if a.count != b.count:
        return False
    if a.category != b.category and not {a.category, b.category} <= _GENERATOR_FAMILY:
        return False
    if a.role != b.role and "unknown" not in (a.role, b.role):
        history = {a.role, b.role} == {"existing", "added"} and any(
            m.role == "added" and PAST_FILING_RE.search(m.snippet) for m in (a, b)
        )
        if not history:
            return False
    for f in ("unit_size", "unit", "manufacturer", "model", "epns", "fuel"):
        x, y = getattr(a, f), getattr(b, f)
        if x is not None and y is not None and x != y:
            return False
    if {a.category, b.category} == _GENERATOR_FAMILY - {EquipmentCategory.GENERATOR}:
        return False  # diesel vs gas: different units
    return True


def _merge(a: EquipmentMention, b: EquipmentMention) -> EquipmentMention:
    """Keep a's evidence, fill in what b adds."""
    category = a.category
    if a.category is EquipmentCategory.GENERATOR:
        category = b.category
    updates = {
        f: getattr(b, f)
        for f in ("unit_size", "unit", "fuel", "manufacturer", "model", "ticker", "epns")
        if getattr(a, f) is None and getattr(b, f) is not None
    }
    if a.role == "unknown" or b.role == "existing":
        updates["role"] = b.role
    return replace(a, category=category, **updates)


def _merge_restatements(mentions: list[EquipmentMention]) -> tuple[list[EquipmentMention], int]:
    """Permits describe the same units more than once ("two (2) 500 kW Caterpillar
    generators" ... "the two 500 kW generators are Tier 3"). Fold those into one row."""
    out: list[EquipmentMention] = []
    merged = 0
    for m in mentions:
        for i, kept in enumerate(out):
            if kept.method == m.method and _same_units(kept, m):
                out[i] = _merge(kept, m)
                merged += 1
                break
        else:
            out.append(m)
    return out, merged


def _maert_category(epn: str, src: str) -> EquipmentCategory | None:
    text = f"{epn} {src}".lower()
    if "fire" in text and "pump" in text:
        return EquipmentCategory.OTHER
    if "turb" in text:
        return EquipmentCategory.GAS_TURBINE
    if re.search(r"\b(?:gen|engine|eg)", text):
        fuel = _fuel(src)
        if fuel == "diesel":
            return EquipmentCategory.DIESEL_GENERATOR
        if fuel == "natural_gas":
            return EquipmentCategory.GAS_GENERATOR
        return EquipmentCategory.GENERATOR
    if "tank" in text or re.search(r"\btk", text):
        return EquipmentCategory.FUEL_TANK
    return None  # cooling towers, fugitives, loading: not equipment we count


def _maert(pages: list[str]) -> list[EquipmentMention]:
    """Count units from the Maximum Allowable Emission Rates Table, one row per unit."""
    groups: dict[tuple, dict[str, Any]] = {}
    for page_no, page in enumerate(pages, start=1):
        heading = MAERT_HEADING_RE.search(page)
        if not heading:
            continue
        for m in MAERT_ROW_RE.finditer(page, heading.end()):
            epn, src = m.group("epn").strip(), m.group("src").strip()
            category = _maert_category(epn, src)
            if category is None:
                continue
            manufacturer, model, ticker = _make_model(src)
            # Rows can wrap ("GE LM2500 simple-" / "cycle"), so group by make and model when
            # known and fall back to the description with any trailing hyphen removed.
            desc = None if model else src.rstrip("-").strip().lower()
            key = (category, manufacturer, model, desc)
            g = groups.setdefault(
                key,
                {
                    "epns": [],
                    "fuel": _fuel(src),
                    "ticker": ticker,
                    "page": page_no,
                    "first_row": m.group(0).strip(),
                },
            )
            if epn not in g["epns"]:
                g["epns"].append(epn)
    mentions = []
    for (category, manufacturer, model, _desc), g in groups.items():
        mentions.append(
            EquipmentMention(
                category=category,
                count=len(g["epns"]),
                unit_size=None,
                unit=None,
                fuel=g["fuel"],
                manufacturer=manufacturer,
                model=model,
                ticker=g["ticker"],
                page=g["page"],
                snippet=f"MAERT rows {g['epns'][0]}..{g['epns'][-1]}: {g['first_row']}",
                method="maert",
            )
        )
    return mentions


def _complete_tables(
    table: list[EquipmentMention],
) -> tuple[list[EquipmentMention], list[EquipmentMention]]:
    """Keep table rows only for categories whose unit numbers run 1..N with no gaps.

    A table that parsed only "EGEN08..EGEN09" missed rows, and trusting it would replace a
    full prose count with 2."""
    by_category: dict[EquipmentCategory, list[EquipmentMention]] = {}
    for m in table:
        by_category.setdefault(m.category, []).append(m)
    keep, partial = [], []
    for rows in by_category.values():
        numbers = []
        for m in rows:
            labels = re.match(r"MAERT rows (.+?)\.\.(.+?):", m.snippet)
            first, last = (int(re.search(r"(\d+)\D*$", x).group(1)) for x in labels.groups())
            numbers.append((first, last, m.count))
        numbers.sort()
        complete = (
            numbers[0][0] == 1
            and all(last - first + 1 == count for first, last, count in numbers)
            and all(numbers[i][1] + 1 == numbers[i + 1][0] for i in range(len(numbers) - 1))
        )
        (keep if complete else partial).extend(rows)
    return keep, partial


def _group_category(unit_id: str) -> EquipmentCategory:
    tail = unit_id.removeprefix("GRP-")
    if tail.startswith("TURB"):
        return EquipmentCategory.GAS_TURBINE
    if tail.startswith("GEN") or tail.startswith("ENG"):
        return EquipmentCategory.GENERATOR
    if "TANK" in tail or "TNK" in tail:
        return EquipmentCategory.FUEL_TANK
    return EquipmentCategory.OTHER


def _unit_groups(pages: list[str]) -> list[dict[str, Any]]:
    found: dict[str, list[int]] = {}
    for page_no, page in enumerate(pages, start=1):
        for m in UNIT_GROUP_RE.finditer(page):
            pages_for = found.setdefault(m.group(), [])
            if page_no not in pages_for:
                pages_for.append(page_no)
    return [
        {"unit_id": uid, "category": str(_group_category(uid)), "pages": found[uid]}
        for uid in sorted(found)
    ]


def _document_date(text: str, today: date | None = None) -> str | None:
    """The latest date in the document that isn't in the future.

    Letters, reviews, and signatures are dated on or after everything they discuss, while
    expiration dates ("August 21, 2034") are in the future and get skipped.
    """
    today = today or date.today()
    months = _MONTHS.split("|")
    found: list[date] = []
    for m in DATE_RE.finditer(text):
        try:
            if m.group("month"):
                d = date(int(m.group("year")), months.index(m.group("month")) + 1, int(m["day"]))
            else:
                d = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
        if d <= today:
            found.append(d)
    return max(found).isoformat() if found else None


def _metadata(text: str, result: AirPermitExtraction) -> None:
    if TITLE_V_SOB_RE.search(text):
        result.document_kind = "title_v_statement_of_basis"
    elif TECH_REVIEW_RE.search(text):
        pbr = PBR_HEADING_RE.search(text)
        result.document_kind = "pbr_technical_review" if pbr else "nsr_technical_review"
    elif REGISTRATION_LETTER_RE.search(text):
        result.document_kind = "standard_permit_registration_letter"
    elif PBR_LETTER_RE.search(text):
        result.document_kind = "pbr_registration_letter"
    result.document_date = _document_date(text)
    result.withdrawn = bool(WITHDRAWN_RE.search(" ".join(text.split())))
    if m := RN_RE.search(text):
        result.rn = f"RN{m.group(1)}"
    if m := CN_RE.search(text):
        result.cn = f"CN{m.group(1)}"

    # Labeled fields ("County: Taylor") beat inferred ones.
    if m := PERMIT_LABEL_RE.search(text) or REGISTRATION_RE.search(text) or PERMIT_RE.search(text):
        result.permit_number = m.group(1).rstrip("-")
    if m := COUNTY_LABEL_RE.search(text):
        result.county = m.group(1)
    else:
        counties = Counter(m.group(1) for m in COUNTY_RE.finditer(text))
        if counties:
            result.county = counties.most_common(1)[0][0]
    if m := SITE_NAME_LABEL_RE.search(text):
        result.site_name = m.group(1)
    if m := NEAREST_CITY_LABEL_RE.search(text):
        result.nearest_city = m.group(1)
    if m := APPLICANT_RE.search(text) or COMPANY_LABEL_RE.search(text):
        result.applicant = m.group(1)
    elif result.document_kind == "title_v_statement_of_basis":
        # The permit holder is the line right under the Statement of Basis heading.
        m = TITLE_V_SOB_RE.search(text)
        line = m.group(1).strip()
        if line and ":" not in line:
            result.applicant = line

    for m in NSR_AUTH_RE.finditer(text):
        auth = {"number": m.group(1), "issued": f"{m.group(4)}-{m.group(2)}-{m.group(3)}"}
        if auth not in result.nsr_authorizations:
            result.nsr_authorizations.append(auth)
    for m in SAME_SITE_PERMIT_RE.finditer(text):
        if m.group(1) not in result.same_site_permits:
            result.same_site_permits.append(m.group(1))
    pbrs = [m.group(1) for m in PBR_RE.finditer(text)]
    for line in PBR_LIST_RE.finditer(text):
        pbrs += re.findall(r"\b106\.\d{3}\b", line.group())
    for pbr in pbrs:
        if pbr not in result.permits_by_rule:
            result.permits_by_rule.append(pbr)


def extract_air_permit(text: str) -> AirPermitExtraction:
    result = AirPermitExtraction()
    _metadata(text, result)
    pages = text.split("\f")
    result.unit_groups = _unit_groups(pages)

    found: list[EquipmentMention] = []
    for page_no, page in enumerate(pages, start=1):
        # The emission rates table is read by _maert, not as prose.
        if heading := MAERT_HEADING_RE.search(page):
            page = page[: heading.start()]
        found += _scan_page(" ".join(page.split()), page_no)
    result.equipment, result.duplicate_mentions = _merge_restatements(found)

    # A MAERT lists every unit, so for any category it covers it replaces this document's
    # prose counts. Otherwise "the 10 natural gas turbines" in the cover letter and the
    # 5 + 5 turbines in the table would be counted as 20.
    table, partial = _complete_tables(_maert(pages))
    result.partial_tables = partial
    covered = {m.category for m in table}
    result.superseded_mentions = [m for m in result.equipment if m.category in covered]
    result.equipment = [m for m in result.equipment if m.category not in covered] + table
    return result
