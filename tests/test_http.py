import httpx
import pytest

from tracker.config import Settings
from tracker.http import PoliteClient, RobotsDisallowed


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def client_for(handler, clock=None):
    clock = clock or FakeClock()
    return PoliteClient(
        Settings(), transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock
    ), clock


def test_rate_limit_two_seconds_per_host():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"ok")

    client, clock = client_for(handler)
    client.get("https://example.gov/a")
    client.get("https://example.gov/b")
    # robots, a, b: each waits a full 2 s after the previous request
    assert clock.sleeps == [2.0, 2.0]


def test_robots_disallow_blocks_fetch():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200)

    client, _ = client_for(handler)
    with pytest.raises(RobotsDisallowed):
        client.get("https://example.gov/private/doc.pdf")
    assert "/private/doc.pdf" not in seen


def test_robots_server_error_means_disallow():
    client, _ = client_for(lambda req: httpx.Response(503))
    with pytest.raises(RobotsDisallowed):
        client.get("https://example.gov/doc.pdf")


def test_retries_transient_errors():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, content=b"done")

    client, _ = client_for(handler)
    assert client.get("https://example.gov/doc").content == b"done"
    assert calls["n"] == 3
