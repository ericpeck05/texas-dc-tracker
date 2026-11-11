"""Claude's pass over the review queue (decided_by=claude), November 2026.

Run once against a database built from the same snapshots; every decision lands in
seeds/review_decisions.csv, so later rebuilds replay it without this script. Items not
named here are left for a person.

Rules:
- same_site: confirm phases or numbered buildings of one campus, and the same named project
  in two sources (same name, county, company); reject pairs in different counties or with a
  different developer or tenant. Ambiguous pairs stay open.
- filing: confirm only when the filing's county settles it.
- parent: confirm when the entity already carries the company's name and shares its office.
- affiliate: confirm when both entities belong to the same company, when their names share a
  distinctive word at a small address, or when the address is one company's own office and
  every entity there is that company's (checked by hand: names, known subsidiaries).
"""

from __future__ import annotations

import re
import sys

from tracker.config import get_settings
from tracker.db.session import make_engine, session_scope
from tracker.review import ReviewError, review_and_save, review_queue

SAME_SITE = {
    # link id: (decision, left name, right name, note)
    53: ("confirm", "Austin Data Center 2", "same 'Austin Data Center 2' project, Travis County"),
    293: ("confirm", "Borden County", "phase 2 of the same Borden County campus"),
    522: ("confirm", "Milam County", "phase 2 of the same Milam County campus"),
    303: ("reject", "Bosque", "different occupants (Amazon vs Google)"),
    341: ("reject", "Houston West", "different counties (Fort Bend vs Harris)"),
    345: ("confirm", "Denton", "Core Scientific's Denton campus in both"),
    431: ("confirm", "Hutto", "Google Hutto campus building LLC"),
    437: ("confirm", "Hutto", "Google Hutto campus building LLC"),
    478: ("confirm", "Childress", "Lancium's Childress campus"),
    562: ("confirm", "Poolside", "Poolside's Pecos County campus, phase I"),
    578: ("confirm", "Goodnight", "Crusoe's Goodnight project"),
    630: ("confirm", "Red Oak", "second building of Compass's Red Oak campus"),
    648: ("confirm", "Corsicana", "Riot's Corsicana campus"),
    656: ("confirm", "Corsicana", "Riot's Corsicana campus"),
    673: ("confirm", "Rockdale", "Riot's Rockdale campus"),
    681: ("confirm", "Rockdale", "Riot's Rockdale campus"),
    702: ("confirm", "Scurry", "building 2 of the same Scurry County campus"),
    727: ("confirm", "Temple", "Meta's Temple data center in both"),
    862: ("reject", "Vantage", "a different regulated entity at Vantage's campus"),
    863: ("reject", "San Antonio Data Center II", "a different regulated entity"),
    880: ("reject", "Vantage", "a different regulated entity at Vantage's campus"),
    881: ("reject", "San Antonio Data Center II", "a different regulated entity"),
    937: ("reject", "Vantage", "a different regulated entity at Vantage's campus"),
    938: ("reject", "San Antonio Data Center II", "a different regulated entity"),
}
FILING = {
    833: ("confirm", "Corsicana", "Navarro County abatement: Riot's Navarro County campus"),
}
# Addresses that are one company's own office, checked by hand.
FAMILY_OFFICES = {
    "1601 WILLOW RD, MENLO PARK": "Meta",
    "2820 NORTHWESTERN PKWY, SANTA CLARA": "Vantage Data Centers",
    "12201 TUKWILA INTERNATIONAL BLVD": "Sabey Data Centers",
    "1600 AMPHITHEATRE PKWY": "Alphabet (Google)",
    "PO BOX 210851, DALLAS": "Stream Data Centers",
    "2880 ZANKER RD STE 205": "Bitdeer",
    "250 VESEY ST FL 3": "Jane Street",
    "7135 S DECATUR BLVD": "Switch",
    "3811 TURTLE CREEK BLVD STE 2100": "Applied Digital",
    "255 FILLMORE ST STE 400": "Crusoe",
    "620 FM 1033, CHILDRESS": "IREN",
    "777 BRICKELL AVE STE 200": "Hut 8",
    "2323 BRYAN ST STE 1800": "Digital Realty",
    "400 S AKARD ST STE 100": "DataBank",
    "1113 VINE ST STE 101": "Enchanted Rock",
    "3855 AMBROSIA ST STE 301": "Riot Platforms",
    "400 N ERVAY ST UNIT 131465": "Prime Data Centers",
    "6711 COLUMBIA GATEWAY DR": "COPT Defense Properties",
    "621 N IRONWOOD DR": "West Texas Digital",
    "319 CLEMATIS ST STE 900": "Vultr (The Constant Company)",
}
_GENERIC = set(
    "llc inc lp the data center centers datacenters holdings co company corp corporation of "
    "and texas tx dc north america american management services properties property partners "
    "group operations llp trust realty energy power digital development".split()
)


def _words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _GENERIC and len(w) > 2}


def _companies(side: dict) -> set[str]:
    return {c["company"] for c in side.get("companies", [])}


def affiliate_call(item: dict) -> str | None:
    left, right, note = item["left"], item["right"], item["note"]
    if _companies(left) & _companies(right):
        return f"both belong to {sorted(_companies(left) & _companies(right))[0]}"
    count = re.search(r"(\d+) taxpayers there", note)
    if _words(left["name"]) & _words(right["name"]) and count and int(count.group(1)) <= 10:
        return "names share a word at a small shared address"
    for office, family in FAMILY_OFFICES.items():
        if office in note:
            return f"shared address is {family}'s own office"
    return None


def main() -> int:
    engine = make_engine(get_settings().db_url)
    done = {"confirm": 0, "reject": 0}
    with session_scope(engine) as session:
        items = {i["link_id"]: i for i in review_queue(session, limit=10_000)["items"]}
        plan: list[tuple[int, str, str]] = []
        for table in (SAME_SITE, FILING):
            for link_id, (decision, must_contain, note) in table.items():
                item = items.get(link_id)
                names = (
                    " | ".join(
                        str(item[side].get("name") or item[side].get("title"))
                        for side in ("left", "right")
                    )
                    if item
                    else ""
                )
                if item is None or must_contain.lower() not in names.lower():
                    print(f"skip {link_id}: not the item reviewed ({names or 'gone'})")
                    continue
                plan.append((link_id, decision, note))
        for item in items.values():
            if item["kind"] == "parent":
                company = item["left"]["name"]
                if company in _companies(item["right"]):
                    plan.append((item["link_id"], "confirm", "entity carries the company's name"))
            elif item["kind"] == "affiliate" and (why := affiliate_call(item)):
                plan.append((item["link_id"], "confirm", why))
        for link_id, decision, note in plan:
            try:
                review_and_save(session, link_id, decision, note, decided_by="claude")
            except ReviewError as e:  # settled by an earlier decision (a merge, a filing)
                print(f"skip {link_id}: {e}")
                continue
            done[decision] += 1
    left = sum(1 for _ in items) - sum(done.values())
    print(f"confirmed {done['confirm']}, rejected {done['reject']}, about {left} left for a person")
    return 0


if __name__ == "__main__":
    sys.exit(main())
