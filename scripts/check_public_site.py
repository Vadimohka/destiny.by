#!/usr/bin/env python3
"""Read-only HTTP checks for the published canonical pages, robots and sitemap.

A passing check proves current accessibility from this runner, not indexing or
access from verified crawler IPs. --dry-run lists targets without network calls.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from build_discovery import Document, load_configs, page_url, plain


USER_AGENT = "DESTINY-SiteCheck/1.0 (+https://destiny.by/)"
MAX_BYTES = 2 * 1024 * 1024
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
BOT_TOKENS = (
    "*", "Googlebot", "Googlebot-Image", "bingbot", "YandexBot", "DuckDuckBot",
    "OAI-SearchBot", "GPTBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
    "Claude-User", "PerplexityBot", "Perplexity-User", "Google-Extended",
)
BLOCKING = re.compile(r"(?:^|[\s,:;])(noindex|none)(?=$|[\s,;])", re.I)


class CheckFailure(Exception):
    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def http_details(status, headers):
    details = [f"HTTP {status}"]
    for key in ("Location", "Server", "CF-Ray"):
        value = headers.get(key) if headers else None
        if value:
            details.append(f"{key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')[:250]}")
    return "; ".join(details)


def fetch(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise CheckFailure(http_details(response.status, response.headers),
                                   retryable=response.status == 429 or 500 <= response.status < 600)
            if response.geturl() != url:
                raise CheckFailure(f"redirected to {response.geturl()}", retryable=False)
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise CheckFailure("response exceeds the 2 MiB check limit", retryable=False)
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset), response.headers
    except HTTPError as error:
        message = http_details(error.code, error.headers)
        error.close()
        raise CheckFailure(message, retryable=error.code == 429 or 500 <= error.code < 600) from error
    except (URLError, TimeoutError, OSError) as error:
        raise CheckFailure(f"network error: {error}") from error
    except (UnicodeError, LookupError) as error:
        raise CheckFailure(f"invalid response encoding: {error}") from error


def require_type(headers, allowed):
    actual = headers.get_content_type().lower()
    if actual not in allowed:
        raise CheckFailure(f"unexpected Content-Type {actual}; expected {', '.join(sorted(allowed))}")


def validate_html(body, headers, *, url, page):
    require_type(headers, {"text/html"})
    for value in headers.get_all("X-Robots-Tag", []):
        if BLOCKING.search(value):
            raise CheckFailure(f"blocking X-Robots-Tag: {value}")
    document = Document(body).root
    for meta in document.all("meta"):
        name = (meta.attrs.get("name") or "").lower()
        if (name == "robots" or "bot" in name) and BLOCKING.search(meta.attrs.get("content") or ""):
            raise CheckFailure(f"blocking {name} meta directive: {meta.attrs.get('content')}")
        if (meta.attrs.get("http-equiv") or "").lower() == "refresh":
            raise CheckFailure("meta refresh on a canonical page")
    canonicals = [node.attrs.get("href") for node in document.all("link")
                  if "canonical" in (node.attrs.get("rel") or "").lower().split()]
    if canonicals != [url]:
        raise CheckFailure(f"expected one self-canonical {url}; got {canonicals}")
    titles = list(document.all("title"))
    if len(titles) != 1 or plain(titles[0]) != page["title"]:
        raise CheckFailure("page title does not match the configured page (stale content or error page)")
    main = document.first(tag="main")
    headings = list(document.all("h1"))
    if main is None or len(plain(main)) < 80 or len(headings) != 1 or not plain(headings[0]):
        raise CheckFailure("missing substantive main content or H1 (possible soft 404)")


def validate_sitemap(body, headers, *, urls):
    require_type(headers, {"application/xml", "text/xml"})
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise CheckFailure(f"invalid sitemap XML: {error}") from error
    if root.tag != f"{{{SITEMAP_NS}}}urlset":
        raise CheckFailure("sitemap must be a urlset in the sitemap 0.9 namespace")
    entries = root.findall(f"{{{SITEMAP_NS}}}url")
    locations = []
    for entry in entries:
        loc = entry.findall(f"{{{SITEMAP_NS}}}loc")
        if len(loc) != 1 or not loc[0].text:
            raise CheckFailure("each sitemap entry must have exactly one nonempty loc")
        locations.append(loc[0].text.strip())
    if len(locations) != len(set(locations)):
        raise CheckFailure("sitemap contains duplicate URLs")
    if set(locations) != set(urls):
        missing, extra = sorted(set(urls) - set(locations)), sorted(set(locations) - set(urls))
        raise CheckFailure(f"sitemap differs from canonical pages; missing={missing}; extra={extra}")


def validate_robots(body, headers, *, urls, bots, sitemap_url):
    require_type(headers, {"text/plain"})
    if not re.search(r"^\s*User-agent\s*:", body, flags=re.M | re.I):
        raise CheckFailure("robots.txt has no User-agent rules")
    robots = RobotFileParser()
    robots.parse(body.splitlines())
    for bot in bots:
        for url in urls:
            if not robots.can_fetch(bot, url):
                raise CheckFailure(f"robots.txt disallows {bot} at {url}")
    if sitemap_url not in (robots.site_maps() or []):
        raise CheckFailure(f"robots.txt does not advertise {sitemap_url}")


def check_url(url, validator, *, attempts=3, timeout=10):
    for attempt in range(1, attempts + 1):
        try:
            body, headers = fetch(url, timeout)
            validator(body, headers)
            return
        except CheckFailure as error:
            if not error.retryable or attempt == attempts:
                raise CheckFailure(f"{url}: {error} (attempt {attempt}/{attempts})",
                                   retryable=False) from error
            time.sleep(2 * attempt)


def targets(configs):
    origin = configs[0]["origin"]
    pages = [(page_url(config, page), page) for config in configs for page in config["pages"]
             if page.get("indexable") is True]
    urls = [url for url, _ in pages]
    if not urls or len(urls) != len(set(urls)) or any(config["origin"] != origin for config in configs):
        raise ValueError("canonical URLs must be nonempty, unique and share one origin")
    bots = sorted(set(BOT_TOKENS) | set(configs[0].get("searchBots", []))
                  | set(configs[0].get("userFetchers", [])))
    sitemap_url = origin + "/sitemap.xml"
    checks = [(url, partial(validate_html, url=url, page=page)) for url, page in pages]
    checks += [(origin + "/robots.txt", partial(validate_robots, urls=urls, bots=bots, sitemap_url=sitemap_url)),
               (sitemap_url, partial(validate_sitemap, urls=urls))]
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, choices=range(1, 4), default=3,
                        help="Maximum attempts per URL, including stale CDN content (default: 3)")
    parser.add_argument("--timeout", type=float, default=10, help="Per-request timeout, at most 10 seconds")
    parser.add_argument("--dry-run", action="store_true", help="List expected URLs without HTTP requests")
    args = parser.parse_args()
    if not 0 < args.timeout <= 10:
        parser.error("--timeout must be greater than zero and at most 10 seconds")
    try:
        checks = targets(load_configs())
    except (KeyError, ValueError, OSError) as error:
        print(f"Public site configuration failed: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"User-Agent: {USER_AGENT}")
        print("\n".join(url for url, _ in checks))
        print(f"Dry run: {len(checks)} URLs; no network requests.")
        return 0
    failures = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(check_url, url, validator, attempts=args.attempts, timeout=args.timeout): url
                   for url, validator in checks}
        for future in as_completed(pending):
            try:
                future.result()
                print(f"OK {pending[future]}")
            except CheckFailure as error:
                failures += 1
                print(f"FAIL {error}", file=sys.stderr)
    print(f"Public site: {len(checks) - failures}/{len(checks)} URLs passed. "
          "This checks current accessibility, not search indexing or verified crawler access.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
