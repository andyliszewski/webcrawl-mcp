"""Unit tests for webcrawl_mcp.scraper covering Issue #1 acceptance criteria."""

from __future__ import annotations

import httpx
import pytest

from webcrawl_mcp import scraper
from webcrawl_mcp.scraper import ScrapeResult, TransportError, _parse_retry_after


URL = "http://test.local/page"
HTML = "<html><body><p>hello</p></body></html>"


# --------------------------------------------------------------------------- #
# AC: defaults are a no-op for current users
# --------------------------------------------------------------------------- #


async def test_defaults_happy_path_returns_static_http(
    queue_responses, stub_extract, fake_firecrawl
):
    queue_responses.append(scraper_response(200, HTML))
    fake_firecrawl.configured = False  # no key configured

    result = await scraper.scrape(URL)

    assert isinstance(result, ScrapeResult)
    assert result.source == "static_http"
    assert result.content.startswith("extracted body")
    assert fake_firecrawl.calls == []  # no outbound firecrawl call


async def test_defaults_403_raises_no_fallback(
    queue_responses, stub_extract, fake_firecrawl
):
    queue_responses.append(scraper_response(403))
    fake_firecrawl.configured = False

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL)
    assert exc.value.status_code == 403
    assert fake_firecrawl.calls == []


async def test_defaults_404_raises_unchanged(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    # Even with the flag set, statuses outside {403,429,503} should not route to fallback.
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "should not be returned"

    queue_responses.append(scraper_response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await scraper.scrape(URL)
    assert fake_firecrawl.calls == []


async def test_defaults_500_raises_unchanged(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "should not be returned"

    queue_responses.append(scraper_response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await scraper.scrape(URL)
    assert fake_firecrawl.calls == []


# --------------------------------------------------------------------------- #
# AC: 403/503 → Firecrawl with provenance
# --------------------------------------------------------------------------- #


async def test_403_routes_to_firecrawl_when_flag_and_key_set(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl markdown"

    queue_responses.append(scraper_response(403))

    result = await scraper.scrape(URL)

    assert result.source == "firecrawl_transport_fallback"
    assert result.content == "firecrawl markdown"
    assert fake_firecrawl.calls == [URL]


async def test_503_routes_to_firecrawl_when_flag_and_key_set(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl markdown"

    queue_responses.append(scraper_response(503))

    result = await scraper.scrape(URL)
    assert result.source == "firecrawl_transport_fallback"
    assert fake_firecrawl.calls == [URL]


async def test_flag_set_but_no_key_raises_with_log(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch, capsys
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = False  # no FIRECRAWL_API_KEY

    queue_responses.append(scraper_response(403))

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL)
    assert exc.value.status_code == 403
    assert fake_firecrawl.calls == []
    captured = capsys.readouterr()
    assert "FALLBACK_ON_TRANSPORT_ERROR=true" in captured.err
    assert "no FIRECRAWL_API_KEY" in captured.err


async def test_firecrawl_returns_none_surfaces_transport_error(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = None  # firecrawl couldn't render either

    queue_responses.append(scraper_response(503))

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL)
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------- #
# AC: 429 + polite_mode + Retry-After
# --------------------------------------------------------------------------- #


async def test_429_polite_retry_succeeds(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    # First 429 with Retry-After: 1, then 200.
    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    queue_responses.append(scraper_response(200, HTML))
    fake_firecrawl.configured = False

    result = await scraper.scrape(URL)
    assert result.source == "static_http_retry"
    assert fake_sleep == [1.0]
    assert fake_firecrawl.calls == []


async def test_429_polite_retry_falls_through_to_firecrawl(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl markdown"

    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))

    result = await scraper.scrape(URL)
    assert result.source == "firecrawl_transport_fallback"
    assert fake_firecrawl.calls == [URL]


async def test_429_polite_retry_falls_through_no_flag_raises(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    fake_firecrawl.configured = False  # no fallback path
    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL)
    assert exc.value.status_code == 429
    assert fake_firecrawl.calls == []


async def test_polite_mode_off_does_not_retry_429(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep, monkeypatch
):
    monkeypatch.setenv("POLITE_MODE", "false")
    fake_firecrawl.configured = False

    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    # No second response queued — if scraper retried, it would RuntimeError
    # from FakeAsyncClient.

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL)
    assert exc.value.status_code == 429
    assert fake_sleep == []  # no polite sleep


async def test_429_without_retry_after_header_no_retry(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    fake_firecrawl.configured = False
    queue_responses.append(scraper_response(429))  # no Retry-After header

    with pytest.raises(TransportError):
        await scraper.scrape(URL)
    assert fake_sleep == []


async def test_retry_after_beyond_timeout_skips_retry(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    # Server returns Retry-After: 9999 with a 2s timeout — retrying after a
    # capped sleep would just earn another 429, so no retry happens and the
    # transport error surfaces. (No second response queued: a retry would
    # RuntimeError in FakeAsyncClient.)
    queue_responses.append(scraper_response(429, headers={"Retry-After": "9999"}))
    fake_firecrawl.configured = False

    with pytest.raises(TransportError) as exc:
        await scraper.scrape(URL, timeout=2)
    assert exc.value.status_code == 429
    assert fake_sleep == []


async def test_retry_after_within_timeout_still_retries(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    # Retry-After at exactly the timeout boundary is honored.
    queue_responses.append(scraper_response(429, headers={"Retry-After": "2"}))
    queue_responses.append(scraper_response(200, HTML))
    fake_firecrawl.configured = False

    result = await scraper.scrape(URL, timeout=2)
    assert result.source == "static_http_retry"
    assert fake_sleep == [2.0]


async def test_retry_after_negative_clamped_to_zero(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    # Hostile server returns a negative Retry-After — must not crash
    # asyncio.sleep, which raises ValueError on negatives.
    queue_responses.append(scraper_response(429, headers={"Retry-After": "-5"}))
    queue_responses.append(scraper_response(200, HTML))
    fake_firecrawl.configured = False

    result = await scraper.scrape(URL)
    assert result.source == "static_http_retry"
    assert all(s >= 0 for s in fake_sleep)


# --------------------------------------------------------------------------- #
# AC: provenance labels everywhere, including quality fallback
# --------------------------------------------------------------------------- #


async def test_quality_fallback_labeled(
    queue_responses, stub_extract, fake_firecrawl
):
    # Force the local extract to return short content so the quality branch fires.
    stub_extract("tiny")  # under MIN_CONTENT_LENGTH
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl rescued long markdown content " * 10

    queue_responses.append(scraper_response(200, HTML))

    result = await scraper.scrape(URL)
    assert result.source == "firecrawl_quality_fallback"
    assert fake_firecrawl.calls == [URL]


async def test_quality_fallback_skipped_when_firecrawl_not_configured(
    queue_responses, stub_extract, fake_firecrawl
):
    stub_extract("tiny")
    fake_firecrawl.configured = False

    queue_responses.append(scraper_response(200, HTML))

    result = await scraper.scrape(URL)
    # Falls back to whatever extraction produced, labeled static_http.
    assert result.source == "static_http"
    assert result.content == "tiny"


# --------------------------------------------------------------------------- #
# AC: cache round-trips provenance
# --------------------------------------------------------------------------- #


async def test_cache_preserves_provenance_on_hit(
    queue_responses, stub_extract, fake_firecrawl, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl markdown"

    queue_responses.append(scraper_response(403))

    first = await scraper.scrape(URL)
    assert first.source == "firecrawl_transport_fallback"

    # Second call should hit cache; do not queue another response.
    second = await scraper.scrape(URL)
    assert second.source == first.source
    assert second.content == first.content
    # Firecrawl should not have been re-invoked.
    assert fake_firecrawl.calls == [URL]


async def test_cache_preserves_static_http_retry_provenance(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep
):
    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    queue_responses.append(scraper_response(200, HTML))
    fake_firecrawl.configured = False

    first = await scraper.scrape(URL)
    assert first.source == "static_http_retry"

    second = await scraper.scrape(URL)
    assert second.source == "static_http_retry"


# --------------------------------------------------------------------------- #
# Provenance values are restricted to the four declared literals
# --------------------------------------------------------------------------- #


VALID_SOURCES = {
    "static_http",
    "static_http_retry",
    "firecrawl_transport_fallback",
    "firecrawl_quality_fallback",
}


async def test_provenance_value_set(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep, monkeypatch
):
    monkeypatch.setenv("FALLBACK_ON_TRANSPORT_ERROR", "true")
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl"

    # Run several scenarios and assert source ∈ valid set every time.
    queue_responses.append(scraper_response(200, HTML))
    r1 = await scraper.scrape("http://test.local/a")
    assert r1.source in VALID_SOURCES

    queue_responses.append(scraper_response(403))
    r2 = await scraper.scrape("http://test.local/b")
    assert r2.source in VALID_SOURCES

    queue_responses.append(scraper_response(429, headers={"Retry-After": "1"}))
    queue_responses.append(scraper_response(200, HTML))
    r3 = await scraper.scrape("http://test.local/c")
    assert r3.source in VALID_SOURCES


# --------------------------------------------------------------------------- #
# prefetched_html — crawler passes HTML it already fetched; no second request
# --------------------------------------------------------------------------- #


async def test_prefetched_html_skips_network_fetch(
    queue_responses, stub_extract, fake_firecrawl
):
    # No responses queued — any network fetch would raise RuntimeError
    # from FakeAsyncClient.
    fake_firecrawl.configured = False

    result = await scraper.scrape(URL, prefetched_html=HTML)

    assert result.source == "static_http"
    assert result.content.startswith("extracted body")


async def test_prefetched_html_still_runs_quality_fallback(
    queue_responses, stub_extract, fake_firecrawl
):
    stub_extract("tiny")  # under MIN_CONTENT_LENGTH
    fake_firecrawl.configured = True
    fake_firecrawl.response = "firecrawl rescued long markdown content " * 10

    result = await scraper.scrape(URL, prefetched_html=HTML)

    assert result.source == "firecrawl_quality_fallback"
    assert fake_firecrawl.calls == [URL]


# --------------------------------------------------------------------------- #
# The rate limiter gate keeps the server's FULL Retry-After so future requests
# to the domain honor it, even when the immediate retry is skipped
# --------------------------------------------------------------------------- #


async def test_rate_limiter_gate_keeps_full_retry_after(
    queue_responses, stub_extract, fake_firecrawl, fake_sleep, monkeypatch
):
    recorded: list[float] = []
    monkeypatch.setattr(
        scraper.rate_limiter,
        "set_retry_after",
        lambda url, seconds: recorded.append(seconds),
    )

    queue_responses.append(scraper_response(429, headers={"Retry-After": "9999"}))
    fake_firecrawl.configured = False

    with pytest.raises(TransportError):
        await scraper.scrape(URL, timeout=2)
    assert recorded == [9999.0]


# --------------------------------------------------------------------------- #
# _parse_retry_after — out-of-scope HTTP-date format
# --------------------------------------------------------------------------- #


def test_parse_retry_after_integer():
    assert _parse_retry_after("5") == 5.0


def test_parse_retry_after_http_date_returns_none():
    # HTTP-date is intentionally not implemented.
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #


def scraper_response(status: int, text: str = "", headers=None):
    """Construct a FakeResponse without importing internals into every test."""
    from tests.conftest import FakeResponse

    return FakeResponse(status, text=text, headers=headers)
