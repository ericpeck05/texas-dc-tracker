"""Project settings. Override paths with TRACKER_DATA_DIR / TRACKER_DB_URL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _env(key: str) -> str | None:
    """From the environment, else from the project's .env (gitignored), so the launchd job
    sees it too."""
    if key in os.environ:
        return os.environ[key]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                return value.strip().strip('"').strip("'")
    return None


PILOT_COUNTIES: tuple[str, ...] = ("Taylor", "Milam", "Bexar", "Tarrant", "Ellis", "Hunt", "Ward")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRACKER_DATA_DIR", "data")))
    min_request_interval: float = 2.0  # seconds between requests to the same host
    user_agent: str = "texas-dc-tracker/0.1 (public-records research)"
    timeout: float = 30.0
    # The SEC asks automated clients to put a contact email in the User-Agent. Set
    # TRACKER_SEC_CONTACT in .env; it is sent to sec.gov only, never stored in the repo.
    sec_contact: str | None = field(default_factory=lambda: _env("TRACKER_SEC_CONTACT"))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_url(self) -> str:
        return os.environ.get("TRACKER_DB_URL", f"sqlite:///{self.data_dir / 'tracker.db'}")


def get_settings() -> Settings:
    return Settings()
