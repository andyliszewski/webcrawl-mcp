# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Floor Starlette at `>=1.0.1` (CVE-2026-48710, "BadHost").** Starlette `< 1.0.1` fails to validate the Host header and poisons `request.url.path`, enabling path-based auth bypass in ASGI middleware. It reaches this server transitively via `fastmcp`/`mcp` (which floor it only at `>=0.27`); the resolved version was `0.52.1`. This server runs over stdio (FastMCP default `mcp.run()`), so it has no HTTP listener and the vector is not exploitable as shipped — this is hygiene: an explicit floor so the resolver cannot regress to a vulnerable build, and so a future SSE/HTTP transport does not inherit it. Local environments resolve to Starlette `1.2.1`.

### Added
- **Transport-failure fallback** — opt-in routing of `403` / `429` / `503` to Firecrawl when `FALLBACK_ON_TRANSPORT_ERROR=true` and `FIRECRAWL_API_KEY` is set. Previously these statuses raised before the fallback could run. Resolves [#1].
- **`POLITE_MODE` env flag** (default `true`) — on a `429` with a parseable `Retry-After`, the original request is retried once after the indicated wait, capped at `REQUEST_TIMEOUT` and clamped to non-negative.
- **Response provenance** — every scrape result reports a `source` field: `static_http`, `static_http_retry`, `firecrawl_transport_fallback`, or `firecrawl_quality_fallback`.
- `tests/` package with unit coverage for the scraper dispatch logic.

### Changed
- **Breaking:** `webcrawl_scrape` now returns `{"content": str, "source": str}` instead of a bare markdown string. Update MCP clients to read `result["content"]`.
- `webcrawl_search` (with `scrape_results=true`) and `webcrawl_crawl` result dicts gain a `source` key on each entry. Additive — clients ignoring unknown keys are unaffected.
- Internal: `scrape()` returns a `ScrapeResult` dataclass; `fetch_url` raises `TransportError` for `{403, 429, 503}` carrying `status_code` and `retry_after` rather than the generic `httpx.HTTPStatusError`.

### Fixed
- The existing extraction-quality fallback now carries a provenance label (`firecrawl_quality_fallback`); previously the dispatch path was opaque to callers.

## [0.1.0] - 2026-04-17

Initial public release.

[Unreleased]: https://github.com/andyliszewski/webcrawl-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/andyliszewski/webcrawl-mcp/releases/tag/v0.1.0
[#1]: https://github.com/andyliszewski/webcrawl-mcp/issues/1
