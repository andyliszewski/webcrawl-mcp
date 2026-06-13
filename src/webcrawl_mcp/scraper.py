"""URL fetching and content extraction."""

import os
import sys
from asyncio import sleep as _async_sleep
from dataclasses import dataclass
from typing import Literal

import httpx
import trafilatura
from markdownify import markdownify as md

from webcrawl_mcp.cache import cache
from webcrawl_mcp.rate_limiter import rate_limiter
from webcrawl_mcp.firecrawl import is_configured as firecrawl_configured, scrape_with_firecrawl

DEFAULT_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
MIN_CONTENT_LENGTH = 200

# Status codes that route to the transport-fallback branch (see Issue #1).
TRANSPORT_FALLBACK_STATUSES = frozenset({403, 429, 503})

DEFAULT_USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; WebcrawlMCP/1.0; +https://github.com/andyliszewski/webcrawl-mcp)",
)


ProvenanceSource = Literal[
    "static_http",
    "static_http_retry",
    "firecrawl_transport_fallback",
    "firecrawl_quality_fallback",
]


@dataclass(frozen=True)
class ScrapeResult:
    """Scrape output with provenance.

    Attributes:
        content: Extracted markdown content
        source: How the content was obtained (see ProvenanceSource)
    """

    content: str
    source: ProvenanceSource


class TransportError(Exception):
    """Raised by fetch_url for statuses in TRANSPORT_FALLBACK_STATUSES.

    Carries status_code and (for 429) parsed Retry-After so callers can
    decide between polite retry, transport fallback, or re-raising.
    """

    def __init__(
        self,
        status_code: int,
        url: str,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"transport error {status_code} for {url}")
        self.status_code = status_code
        self.url = url
        self.retry_after = retry_after


def _is_low_quality(content: str | None) -> bool:
    """True if content is missing or shorter than MIN_CONTENT_LENGTH."""
    if not content:
        return True
    if len(content) < MIN_CONTENT_LENGTH:
        return True
    return False


def _parse_retry_after(value: str) -> float | None:
    """Parse Retry-After header value as seconds.

    HTTP-date format is intentionally not supported; returns None for it.
    """
    try:
        return float(value)
    except ValueError:
        return None


def _fallback_on_transport_error() -> bool:
    """Read FALLBACK_ON_TRANSPORT_ERROR env flag (default false)."""
    return os.environ.get("FALLBACK_ON_TRANSPORT_ERROR", "false").lower() == "true"


def _polite_mode() -> bool:
    """Read POLITE_MODE env flag (default true)."""
    return os.environ.get("POLITE_MODE", "true").lower() != "false"


async def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch URL content using httpx.

    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Raw HTML content for 2xx responses.

    Raises:
        TransportError: For statuses in TRANSPORT_FALLBACK_STATUSES; carries
            status_code and retry_after.
        httpx.HTTPError: For other request failures.
    """
    await rate_limiter.wait_if_needed(url)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
        )

        rate_limiter.record_request(url)

        retry_after: float | None = None
        if response.status_code == 429:
            header = response.headers.get("Retry-After")
            if header:
                seconds = _parse_retry_after(header)
                if seconds is not None:
                    retry_after = seconds
                    rate_limiter.set_retry_after(url, max(0.0, seconds))

        if response.status_code in TRANSPORT_FALLBACK_STATUSES:
            raise TransportError(
                status_code=response.status_code,
                url=url,
                retry_after=retry_after,
            )

        response.raise_for_status()
        return response.text


def extract_with_trafilatura(html: str, url: str) -> str | None:
    """Extract main content as markdown via trafilatura."""
    return trafilatura.extract(
        html,
        url=url,
        include_links=True,
        include_formatting=True,
        include_images=False,
        output_format="markdown",
    )


def extract_with_markdownify(html: str) -> str:
    """Convert full HTML to markdown via markdownify."""
    return md(html, heading_style="ATX", strip=["script", "style", "nav", "footer"])


def _extract(html: str, url: str) -> str:
    """Run local extraction (trafilatura, falling back to markdownify)."""
    content = extract_with_trafilatura(html, url)
    if content and len(content) >= MIN_CONTENT_LENGTH:
        print(
            f"[webcrawl] trafilatura: {len(content)} chars from {url}",
            file=sys.stderr,
        )
        return content

    reason = "no content" if not content else f"only {len(content)} chars"
    print(
        f"[webcrawl] trafilatura {reason}, falling back to markdownify for {url}",
        file=sys.stderr,
    )

    content = extract_with_markdownify(html)
    print(
        f"[webcrawl] markdownify: {len(content)} chars from {url}",
        file=sys.stderr,
    )
    return content


async def _fetch_html_or_fallback(
    url: str, timeout: int
) -> tuple[Literal["static", "firecrawl"], str, ProvenanceSource]:
    """Fetch HTML, applying polite retry and transport fallback per Issue #1.

    Returns:
        ("static", html, source)       — local extraction should run on html
        ("firecrawl", content, source) — content is final; skip local extraction

    Raises:
        TransportError: When neither polite retry nor transport fallback yields
            content (or fallback is disabled).
        httpx.HTTPError: For non-fallback transport failures.
    """
    try:
        html = await fetch_url(url, timeout)
        return ("static", html, "static_http")
    except TransportError as err:
        # Polite retry: 429 with parseable Retry-After gets one bounded retry,
        # but only when the server's ask fits within our timeout — retrying
        # early against a longer Retry-After would just earn another 429.
        # The full value stays in the rate limiter either way, so future
        # requests to the domain honor the server's ask.
        if (
            err.status_code == 429
            and _polite_mode()
            and err.retry_after is not None
            and err.retry_after <= timeout
        ):
            # Clamp negatives to 0 so asyncio.sleep doesn't raise.
            wait = max(0.0, err.retry_after)
            print(
                f"[webcrawl] 429 Retry-After {err.retry_after}s; polite retry "
                f"after {wait:.1f}s for {url}",
                file=sys.stderr,
            )
            await _async_sleep(wait)
            try:
                html = await fetch_url(url, timeout)
                return ("static", html, "static_http_retry")
            except TransportError as retry_err:
                err = retry_err  # fall through to transport-fallback decision

        if _fallback_on_transport_error():
            if firecrawl_configured():
                print(
                    f"[webcrawl] transport error {err.status_code}; "
                    f"falling back to Firecrawl for {url}",
                    file=sys.stderr,
                )
                firecrawl_content = await scrape_with_firecrawl(url, timeout)
                if firecrawl_content:
                    return (
                        "firecrawl",
                        firecrawl_content,
                        "firecrawl_transport_fallback",
                    )
                # Firecrawl returned None — surface the original transport error.
                raise err
            print(
                f"[webcrawl] FALLBACK_ON_TRANSPORT_ERROR=true but no "
                f"FIRECRAWL_API_KEY; raising {err.status_code} for {url}",
                file=sys.stderr,
            )

        raise err


async def scrape(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    prefetched_html: str | None = None,
) -> ScrapeResult:
    """Fetch URL and extract main content as markdown.

    Dispatch:
    - 2xx → local extraction (trafilatura → markdownify); Firecrawl as a
      quality fallback if the result is below MIN_CONTENT_LENGTH.
    - {403, 429, 503} → polite retry (429 only) and/or Firecrawl transport
      fallback, gated on POLITE_MODE and FALLBACK_ON_TRANSPORT_ERROR.

    See Issue #1 for design rationale.

    Args:
        url: The URL to scrape
        timeout: Request timeout in seconds
        prefetched_html: HTML already fetched for this URL (e.g. by the
            crawler for link extraction); skips the network fetch so the
            page isn't requested twice.

    Returns:
        ScrapeResult carrying content and provenance source.
    """
    cached = cache.get(url)
    if cached is not None:
        return cached

    if prefetched_html is not None:
        kind, payload, source = "static", prefetched_html, "static_http"
    else:
        kind, payload, source = await _fetch_html_or_fallback(url, timeout)

    if kind == "firecrawl":
        result = ScrapeResult(content=payload, source=source)
        cache.set(url, result)
        return result

    content = _extract(payload, url)

    if _is_low_quality(content) and firecrawl_configured():
        print(
            f"[webcrawl] content still low quality, trying Firecrawl for {url}",
            file=sys.stderr,
        )
        firecrawl_content = await scrape_with_firecrawl(url, timeout)
        if firecrawl_content and len(firecrawl_content) > len(content or ""):
            result = ScrapeResult(
                content=firecrawl_content,
                source="firecrawl_quality_fallback",
            )
            cache.set(url, result)
            return result

    result = ScrapeResult(content=content, source=source)
    cache.set(url, result)
    return result
