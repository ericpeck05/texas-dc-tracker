"""TCEQ air permit collector.

For now this pulls a curated list of document URLs from seeds/tceq_air.csv. Automatic
discovery from TCEQ's pending-permit lists and Central Registry gets added once those
pages have been inspected by hand (their query formats are not guessed here).
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from tracker.http import PoliteClient, RobotsDisallowed

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Seed:
    url: str
    county: str | None
    note: str | None


@dataclass(frozen=True)
class Fetched:
    seed: Seed
    content: bytes
    content_type: str | None


def read_seeds(path: Path) -> list[Seed]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            Seed(
                url=row["url"].strip(),
                county=row.get("county") or None,
                note=row.get("note") or None,
            )
            for row in csv.DictReader(fh)
            if row.get("url", "").strip() and not row["url"].lstrip().startswith("#")
        ]


def fetch_seeds(
    client: PoliteClient, seeds: list[Seed]
) -> Iterator[Fetched | tuple[Seed, Exception]]:
    for seed in seeds:
        try:
            resp = client.get(seed.url)
        except (httpx.HTTPError, RobotsDisallowed) as exc:
            log.warning("failed %s: %s", seed.url, exc)
            yield seed, exc
            continue
        yield Fetched(seed, resp.content, resp.headers.get("content-type"))
