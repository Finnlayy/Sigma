"""ngrok public URL resolver: payload parse, fallback, TTL cache."""
from __future__ import annotations

from app.core import blueprint as bp
from app.core.public_url import (
    PublicUrl,
    is_loopback,
    reset_public_url_cache,
    resolve_public_url,
)


NGROK_HTTPS = {
    "tunnels": [
        {"public_url": "http://abc.ngrok-free.app", "proto": "http"},
        {"public_url": "https://abc.ngrok-free.app", "proto": "https"},
    ]
}


def setup_function():
    reset_public_url_cache()


def teardown_function():
    reset_public_url_cache()


def test_parses_https_tunnel():
    got = resolve_public_url(now=1.0, fetch=lambda url: NGROK_HTTPS, force=True)
    assert got.url == "https://abc.ngrok-free.app"
    assert got.source == "ngrok"
    assert got.changed is False


def test_fallback_when_ngrok_raises(monkeypatch):
    monkeypatch.setenv("SIGMA_PUBLIC_URL", "https://example.test")

    def boom(_url):
        raise OSError("down")

    got = resolve_public_url(now=1.0, fetch=boom, force=True)
    assert got.url == "https://example.test"
    assert got.source == "env"


def test_ttl_cache_skips_second_fetch():
    calls = {"n": 0}

    def fetch(_url):
        calls["n"] += 1
        return NGROK_HTTPS

    first = resolve_public_url(now=10.0, fetch=fetch, force=True)
    second = resolve_public_url(now=10.0 + bp.PUBLIC_URL_TTL_S - 1, fetch=fetch)
    assert first.url == second.url
    assert calls["n"] == 1
    assert second.source == "cache"


def test_ttl_expiry_refetches_and_flags_change():
    payload = {"tunnels": [{"public_url": "https://one.ngrok-free.app", "proto": "https"}]}

    def fetch(_url):
        return payload

    resolve_public_url(now=1.0, fetch=fetch, force=True)
    payload["tunnels"][0]["public_url"] = "https://two.ngrok-free.app"
    later = resolve_public_url(now=1.0 + bp.PUBLIC_URL_TTL_S + 1, fetch=fetch)
    assert later.url == "https://two.ngrok-free.app"
    assert later.changed is True


def test_is_loopback():
    assert is_loopback("http://127.0.0.1:8000")
    assert is_loopback("https://localhost:8000/api")
    assert is_loopback("http://0.0.0.0:8000")
    assert not is_loopback("https://abc.ngrok-free.app")


def test_public_url_to_dict():
    assert PublicUrl("https://x", "ngrok", True).to_dict() == {
        "url": "https://x", "source": "ngrok", "changed": True,
    }
