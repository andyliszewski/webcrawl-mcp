"""URL discovery and multi-page crawling."""

import fnmatch
import sys
from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from webcrawl_mcp.scraper import fetch_url, scrape


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract all links from HTML.

    Args:
        html: Raw HTML content
        base_url: Base URL for resolving relative links

    Returns:
        List of absolute URLs found in the page
    """
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]

        # Skip non-http links
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        # Resolve relative URLs
        absolute_url = urljoin(base_url, href)

        # Remove fragments
        parsed = urlparse(absolute_url)
        clean_url = parsed._replace(fragment="").geturl()

        links.append(clean_url)

    return links


def filter_same_domain(urls: list[str], base_url: str) -> list[str]:
    """Filter URLs to same domain as base URL.

    Args:
        urls: List of URLs to filter
        base_url: Base URL to match domain against

    Returns:
        URLs that are on the same domain
    """
    base_domain = urlparse(base_url).netloc.lower()

    same_domain = []
    for url in urls:
        url_domain = urlparse(url).netloc.lower()
        if url_domain == base_domain:
            same_domain.append(url)

    return same_domain


async def map_urls(url: str, limit: int = 50) -> list[str]:
    """Discover URLs on a website.

    Fetches the given URL and extracts all same-domain links.

    Args:
        url: Starting URL to map from
        limit: Maximum number of URLs to return

    Returns:
        List of unique same-domain URLs found
    """
    print(f"[webcrawl] mapping URLs from {url}", file=sys.stderr)

    html = await fetch_url(url)
    all_links = extract_links(html, url)
    same_domain = filter_same_domain(all_links, url)

    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for link in same_domain:
        if link not in seen:
            seen.add(link)
            unique_urls.append(link)
            if len(unique_urls) >= limit:
                break

    print(f"[webcrawl] found {len(unique_urls)} unique URLs", file=sys.stderr)
    return unique_urls


def extract_title(html: str) -> str:
    """Extract page title from HTML.

    Args:
        html: Raw HTML content

    Returns:
        Page title or empty string if not found
    """
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()
    return ""


def matches_patterns(url: str, patterns: list[str] | None) -> bool:
    """Check if URL matches any of the glob patterns.

    Args:
        url: URL to check
        patterns: List of glob patterns (e.g., ["*/docs/*", "*/api/*"])

    Returns:
        True if no patterns specified or URL matches at least one pattern
    """
    if not patterns:
        return True

    for pattern in patterns:
        if fnmatch.fnmatch(url, pattern):
            return True
    return False


async def crawl(
    url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    include_patterns: list[str] | None = None,
) -> list[dict]:
    """Crawl multiple pages using BFS.

    Args:
        url: Starting URL
        max_pages: Maximum number of pages to fetch
        max_depth: Maximum link depth from start
        include_patterns: Glob patterns for URLs to include

    Returns:
        List of {url, title, content} dicts for each crawled page
    """
    print(f"[webcrawl] crawling from {url} (max_pages={max_pages}, max_depth={max_depth})", file=sys.stderr)

    results = []
    visited = set()
    # Queue contains (url, depth) tuples
    queue = deque([(url, 0)])

    while queue and len(results) < max_pages:
        current_url, depth = queue.popleft()

        # Skip if already visited
        if current_url in visited:
            continue
        visited.add(current_url)

        # Check pattern filter
        if not matches_patterns(current_url, include_patterns):
            continue

        try:
            # Fetch the page once; reuse the HTML for both link/title
            # extraction and content extraction.
            html = await fetch_url(current_url)
            title = extract_title(html)

            scraped = await scrape(current_url, prefetched_html=html)

            results.append({
                "url": current_url,
                "title": title,
                "content": scraped.content,
                "source": scraped.source,
            })

            print(
                f"[webcrawl] crawled {len(results)}/{max_pages}: {current_url}",
                file=sys.stderr,
            )

            # Add links to queue if not at max depth
            if depth < max_depth:
                links = extract_links(html, current_url)
                same_domain = filter_same_domain(links, url)

                for link in same_domain:
                    if link not in visited:
                        queue.append((link, depth + 1))

        except Exception as e:
            print(f"[webcrawl] failed to crawl {current_url}: {e}", file=sys.stderr)
            continue

    print(f"[webcrawl] crawl complete: {len(results)} pages", file=sys.stderr)
    return results
