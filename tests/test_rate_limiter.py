"""Unit tests for webcrawl_mcp.rate_limiter concurrency behavior."""

from __future__ import annotations

import asyncio
import time

from webcrawl_mcp.rate_limiter import RateLimiter

URL = "http://example.com/page"


async def test_concurrent_same_domain_calls_are_spaced():
    """Concurrent callers must not stampede: each is spaced by >= delay."""
    limiter = RateLimiter(delay=0.05)
    release_times: list[float] = []

    async def hit() -> None:
        await limiter.wait_if_needed(URL)
        release_times.append(time.time())

    await asyncio.gather(hit(), hit(), hit())

    release_times.sort()
    assert release_times[1] - release_times[0] >= 0.04
    assert release_times[2] - release_times[1] >= 0.04


async def test_different_domains_do_not_block_each_other():
    limiter = RateLimiter(delay=5.0)

    start = time.time()
    await asyncio.gather(
        limiter.wait_if_needed("http://a.example/x"),
        limiter.wait_if_needed("http://b.example/x"),
    )
    assert time.time() - start < 1.0


async def test_retry_after_gate_enforced_then_cleared():
    limiter = RateLimiter(delay=0.0)
    limiter.set_retry_after(URL, 0.05)

    start = time.time()
    await limiter.wait_if_needed(URL)
    assert time.time() - start >= 0.04

    # Gate is consumed; the next call must not wait again.
    start = time.time()
    await limiter.wait_if_needed(URL)
    assert time.time() - start < 0.04
