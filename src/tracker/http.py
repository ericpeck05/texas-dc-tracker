"""A polite HTTP client: honors robots.txt, rate limits per host, retries transient errors.

Every collector goes through this client so the etiquette rules live in one place.
It never sends credentials and never follows a login flow.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from tracker.config import Settings, get_settings

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    """robots.txt does not allow fetching this URL."""


class _DisallowAll:
    """Stand-in parser used when robots.txt can't be read (RFC 9309: 5xx/unreachable)."""

    def can_fetch(self, useragent: str, url: str) -> bool:
        return False


class PoliteClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 3,
    ) -> None:
        self.settings = settings or get_settings()
        self._sleep = sleep
        self._clock = clock
        self.max_retries = max_retries
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | _DisallowAll | None] = {}
        self._client = httpx.Client(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.timeout,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.settings.min_request_interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _robots_for(self, url: str) -> RobotFileParser | _DisallowAll | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        self._wait_turn(parts.netloc)
        parser: RobotFileParser | _DisallowAll | None
        try:
            resp = self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            parser = _DisallowAll()
        else:
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
            elif 400 <= resp.status_code < 500:
                parser = None  # no robots.txt: everything allowed
            else:
                parser = _DisallowAll()
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return parser is None or parser.can_fetch(self.settings.user_agent, url)

    def get(self, url: str) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows (or could not be read for) {url}")
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            self._wait_turn(host)
            try:
                resp = self._client.get(url)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self._sleep(2.0 * 2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(2.0 * 2**attempt)
                continue
            resp.raise_for_status()
            return resp
        raise AssertionError("unreachable")
