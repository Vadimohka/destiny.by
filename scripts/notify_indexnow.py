#!/usr/bin/env python3
"""Notify IndexNow after publication; --dry-run never makes network requests."""

import argparse
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_FILENAME = "indexnow-key.txt"
TIMEOUT_SECONDS = 10


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def warning(message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning title=IndexNow::{escaped}")


def payload_from_source():
    config = json.loads((ROOT / "seo/site.json").read_text(encoding="utf-8"))
    origin = config["origin"].rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("seo/site.json origin must be an HTTPS origin without a path or credentials.")

    key = (ROOT / KEY_FILENAME).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
        raise ValueError("The published IndexNow key must contain 8–128 letters, digits or dashes.")

    urls = []
    for page in config["pages"]:
        if page.get("indexable") is not True:
            continue
        path = page["path"]
        relative = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or relative.scheme
            or relative.netloc
            or relative.query
            or relative.fragment
        ):
            raise ValueError("Indexable pages must have canonical local paths without query strings or fragments.")
        url = origin + path
        if url not in urls:
            urls.append(url)

    if not urls or len(urls) > 10000:
        raise ValueError("IndexNow requires between 1 and 10,000 indexable URLs per request.")

    return {
        "host": parsed.hostname,
        "key": key,
        "keyLocation": origin + "/" + KEY_FILENAME,
        "urlList": urls,
    }


def notify(payload):
    headers = {"User-Agent": f"DESTINY-IndexNow/1.0 (+https://{payload['host']}/)"}
    key_request = Request(payload["keyLocation"], headers=headers)
    try:
        with OPENER.open(key_request, timeout=TIMEOUT_SECONDS) as response:
            published = response.read(1024).decode("utf-8").strip()
            if response.status != 200 or response.geturl() != payload["keyLocation"]:
                warning("Public key URL did not return HTTP 200 at its canonical address. No URLs were submitted.")
                return 1
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError):
        warning("Public key could not be read after deployment. CDN propagation may be pending. No URLs were submitted.")
        return 1

    if published != payload["key"]:
        warning("Published key does not match the repository key. CDN propagation may be pending. No URLs were submitted.")
        return 1

    request = Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    except (URLError, TimeoutError, OSError):
        warning("IndexNow request could not be completed. Site deployment is unaffected; no automatic retry is performed.")
        return 1

    if status == 200:
        print(f"IndexNow received {len(payload['urlList'])} public URLs. Receipt does not guarantee indexing.")
        return 0
    if status == 202:
        print(f"IndexNow received {len(payload['urlList'])} public URLs; key validation is pending. Indexing is not confirmed.")
        return 0
    warning(f"IndexNow returned HTTP {status}. Site deployment is unaffected; no automatic retry is performed.")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the target and public URLs without any network requests.")
    args = parser.parse_args()
    try:
        payload = payload_from_source()
    except (KeyError, TypeError, ValueError, OSError) as error:
        warning(f"Configuration error: {error}")
        return 1

    if args.dry_run:
        print(json.dumps({"endpoint": ENDPOINT, **payload, "key": "[public value in indexnow-key.txt]"}, ensure_ascii=False, indent=2))
        print("Dry run: no network requests or submissions were made.")
        return 0
    return notify(payload)


if __name__ == "__main__":
    raise SystemExit(main())
