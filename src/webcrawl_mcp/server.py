"""Webcrawl MCP Server - Web scraping and search capabilities."""

import sys

from fastmcp import FastMCP

from webcrawl_mcp.scraper import scrape, DEFAULT_TIMEOUT
from webcrawl_mcp.search import search, search_and_scrape
from webcrawl_mcp.crawler import map_urls, crawl

mcp = FastMCP("Webcrawl")


@mcp.tool
async def webcrawl_scrape(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch a URL and extract main content as markdown.

    Args:
        url: The URL to scrape
        timeout: Request timeout in seconds (default: 30)

    Returns:
        Dict with:
          - content: markdown of the page's main content
          - source:  one of "static_http", "static_http_retry",
                     "firecrawl_transport_fallback", "firecrawl_quality_fallback"
                     (see Issue #1)
    """
    result = await scrape(url, timeout)
    return {"content": result.content, "source": result.source}


@mcp.tool
async def webcrawl_search(
    query: str, num_results: int = 5, scrape_results: bool = False
) -> list[dict]:
    """Search the web using DuckDuckGo.

    Args:
        query: Search query string
        num_results: Maximum number of results to return (default: 5)
        scrape_results: If true, fetch full page content for each result (default: false)

    Returns:
        List of search results, each with url, title, snippet, and optionally content
    """
    if scrape_results:
        return await search_and_scrape(query, num_results)
    return await search(query, num_results)


@mcp.tool
async def webcrawl_map(url: str, limit: int = 50) -> list[str]:
    """Discover URLs on a website.

    Fetches the given URL and extracts all same-domain links.

    Args:
        url: Starting URL to map from
        limit: Maximum number of URLs to return (default: 50)

    Returns:
        List of unique same-domain URLs found on the page
    """
    return await map_urls(url, limit)


@mcp.tool
async def webcrawl_crawl(
    url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    include_patterns: list[str] | None = None,
) -> list[dict]:
    """Crawl multiple pages starting from a URL.

    Uses BFS to discover and fetch pages up to max_depth links away.
    Respects rate limiting between requests.

    Args:
        url: Starting URL
        max_pages: Maximum number of pages to fetch (default: 10)
        max_depth: Maximum link depth from start (default: 2)
        include_patterns: Glob patterns for URLs to include (e.g., ["*/docs/*"])

    Returns:
        List of {url, title, content} for each crawled page
    """
    return await crawl(url, max_pages, max_depth, include_patterns)


def main() -> None:
    """Entry point for the MCP server."""
    print("Webcrawl MCP server running", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
