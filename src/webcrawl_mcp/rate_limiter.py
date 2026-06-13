"""Per-domain rate limiting for HTTP requests."""

import asyncio
import os
import sys
import time
from urllib.parse import urlparse

DEFAULT_RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", "1.0"))


class RateLimiter:
    """Per-domain rate limiter with configurable delay."""

    def __init__(self, delay: float = DEFAULT_RATE_LIMIT_DELAY):
        """Initialize rate limiter.

        Args:
            delay: Minimum seconds between requests to same domain
        """
        self.delay = delay
        self._last_request: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    async def wait_if_needed(self, url: str) -> None:
        """Wait if necessary to respect rate limit for domain.

        Same-domain callers are serialized via a per-domain lock; without it,
        concurrent tool calls would all observe the same last-request time,
        wait the same amount, and then fire simultaneously.

        Args:
            url: URL about to be requested
        """
        domain = self._get_domain(url)
        lock = self._locks.setdefault(domain, asyncio.Lock())

        async with lock:
            # Check for Retry-After
            if domain in self._retry_after:
                wait_time = self._retry_after[domain] - time.time()
                if wait_time > 0:
                    print(
                        f"[webcrawl] rate limit: waiting {wait_time:.1f}s (Retry-After) for {domain}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(wait_time)
                del self._retry_after[domain]

            # Check normal rate limit
            if domain in self._last_request:
                elapsed = time.time() - self._last_request[domain]
                if elapsed < self.delay:
                    wait_time = self.delay - elapsed
                    print(
                        f"[webcrawl] rate limit: waiting {wait_time:.1f}s for {domain}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(wait_time)

            # Reserve the slot before releasing the lock so the next caller
            # spaces off this request even before its response arrives.
            self._last_request[domain] = time.time()

    def record_request(self, url: str) -> None:
        """Record that a request was made to this URL's domain.

        Args:
            url: URL that was requested
        """
        domain = self._get_domain(url)
        self._last_request[domain] = time.time()

    def set_retry_after(self, url: str, seconds: float) -> None:
        """Set Retry-After delay for a domain.

        Args:
            url: URL that returned Retry-After
            seconds: Seconds to wait before next request
        """
        domain = self._get_domain(url)
        self._retry_after[domain] = time.time() + seconds
        print(
            f"[webcrawl] rate limit: Retry-After {seconds}s set for {domain}",
            file=sys.stderr,
        )


# Global rate limiter instance
rate_limiter = RateLimiter()
