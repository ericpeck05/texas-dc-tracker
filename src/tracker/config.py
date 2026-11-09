"""Project settings. Override paths with TRACKER_DATA_DIR / TRACKER_DB_URL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PILOT_COUNTIES: tuple[str, ...] = ("Taylor", "Milam", "Bexar", "Tarrant", "Ellis", "Hunt", "Ward")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRACKER_DATA_DIR", "data")))
    min_request_interval: float = 2.0  # seconds between requests to the same host
    user_agent: str = "texas-dc-tracker/0.1 (public-records research)"
    timeout: float = 30.0

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_url(self) -> str:
        return os.environ.get("TRACKER_DB_URL", f"sqlite:///{self.data_dir / 'tracker.db'}")


def get_settings() -> Settings:
    return Settings()
