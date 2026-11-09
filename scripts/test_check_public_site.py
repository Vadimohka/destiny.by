"""Regression tests for live-indexability checks; no external network calls."""

from email.message import Message
from functools import partial
import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import check_public_site as check


URL = "https://destiny.by/privacy.html"
PAGE = {"title": "Privacy — DESTINY"}
HTML = ('<html><head><title>Privacy — DESTINY</title>'
        '<meta name="robots" content="index, follow">'
        f'<link rel="canonical" href="{URL}"></head>'
        '<body><main><h1>Privacy</h1><p>' + "Useful public policy content. " * 8 + '</p></main></body></html>')


def headers(content_type="text/html", extra=()):
    result = Message()
    result["Content-Type"] = content_type + "; charset=utf-8"
    for name, value in extra:
        result[name] = value
    return result


class Response(io.BytesIO):
    def __init__(self, body=HTML, *, url=URL, status=200, response_headers=None):
        super().__init__(body.encode("utf-8"))
        self.url, self.status = url, status
        self.headers = response_headers or headers()

    def geturl(self):
        return self.url


class HtmlChecks(unittest.TestCase):
    def validate(self, body=HTML, response_headers=None):
        check.validate_html(body, response_headers or headers(), url=URL, page=PAGE)

    def test_valid_page(self):
        self.validate()

    def test_noindex_and_none_for_general_and_specific_bots(self):
        for name, content in (("ROBOTS", "NoIndex, follow"), ("Googlebot", "NONE"),
                              ("Bingbot", "noindex"), ("googlebot-news", "noindex")):
            with self.subTest(name=name):
                with self.assertRaisesRegex(check.CheckFailure, "blocking"):
                    self.validate(HTML.replace("</head>", f'<meta name="{name}" content="{content}"></head>'))

    def test_repeated_x_robots_headers_are_all_checked(self):
        with self.assertRaisesRegex(check.CheckFailure, "X-Robots-Tag"):
            self.validate(response_headers=headers(extra=[("X-Robots-Tag", "index, follow"),
                                                          ("x-robots-tag", "Googlebot: NoIndex")]))

    def test_none_x_robots_directive(self):
        with self.assertRaisesRegex(check.CheckFailure, "X-Robots-Tag"):
            self.validate(response_headers=headers(extra=[("X-Robots-Tag", "none")]))

    def test_wrong_canonical_and_duplicate_canonical(self):
        for body in (HTML.replace(URL, "https://destiny.by/"),
                     HTML.replace("</head>", f'<link rel="canonical" href="{URL}"></head>')):
            with self.subTest(body=body):
                with self.assertRaisesRegex(check.CheckFailure, "self-canonical"):
                    self.validate(body)

    def test_error_page_title_and_empty_main(self):
        with self.assertRaisesRegex(check.CheckFailure, "title"):
            self.validate(HTML.replace("<title>Privacy — DESTINY", "<title>Page not found"))
        with self.assertRaisesRegex(check.CheckFailure, "soft 404"):
            self.validate(HTML.replace("<main>", "<section>").replace("</main>", "</section>"))

    def test_meta_refresh_and_non_html(self):
        with self.assertRaisesRegex(check.CheckFailure, "meta refresh"):
            self.validate(HTML.replace("</head>", '<meta http-equiv="Refresh" content="0;url=/"></head>'))
        with self.assertRaisesRegex(check.CheckFailure, "Content-Type"):
            self.validate(response_headers=headers("application/json"))


class HttpChecks(unittest.TestCase):
    def run_check(self, **kwargs):
        check.check_url(URL, partial(check.validate_html, url=URL, page=PAGE), **kwargs)

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_transient_server_error_retries_then_passes(self, opened, sleep):
        opened.side_effect = [HTTPError(URL, 503, "Unavailable", headers(), None), Response()]
        self.run_check()
        self.assertEqual(opened.call_count, 2)
        sleep.assert_called_once_with(2)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("User-agent"), check.USER_AGENT)
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_network_error_and_429_retry(self, opened, sleep):
        opened.side_effect = [URLError("timeout"), HTTPError(URL, 429, "Busy", headers(), None), Response()]
        self.run_check()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_stale_noindex_is_retried(self, opened, sleep):
        opened.side_effect = [Response(HTML.replace("index, follow", "noindex, follow")), Response()]
        self.run_check()
        self.assertEqual(opened.call_count, 2)

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_redirect_fails_without_follow_or_retry(self, opened, sleep):
        opened.side_effect = HTTPError(URL, 301, "Moved", headers(extra=[("Location", "/")]), None)
        with self.assertRaisesRegex(check.CheckFailure, r"privacy.html: HTTP 301; Location=/"):
            self.run_check()
        opened.assert_called_once()
        sleep.assert_not_called()
        self.assertIsNone(check.NoRedirect().redirect_request(None, None, 301, "Moved", {}, "/"))

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_cloudflare_block_is_identified_without_retry(self, opened, sleep):
        opened.side_effect = HTTPError(URL, 403, "Forbidden", headers(extra=[("Server", "cloudflare"),
                                                                                 ("CF-Ray", "test-ray")]), None)
        with self.assertRaisesRegex(check.CheckFailure, r"HTTP 403; Server=cloudflare; CF-Ray=test-ray"):
            self.run_check()
        opened.assert_called_once()
        sleep.assert_not_called()

    @patch.object(check.time, "sleep")
    @patch.object(check.OPENER, "open")
    def test_persistent_5xx_is_bounded(self, opened, sleep):
        opened.side_effect = [HTTPError(URL, 522, "Timeout", headers(), None) for _ in range(3)]
        with self.assertRaisesRegex(check.CheckFailure, r"HTTP 522 \(attempt 3/3\)"):
            self.run_check()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(check.OPENER, "open")
    def test_unexpected_followed_redirect_is_rejected(self, opened):
        opened.return_value = Response(url="https://destiny.by/")
        with self.assertRaisesRegex(check.CheckFailure, "redirected to"):
            self.run_check()


class DiscoveryChecks(unittest.TestCase):
    def sitemap(self, urls=(URL,)):
        return (f'<urlset xmlns="{check.SITEMAP_NS}">'
                + "".join(f"<url><loc>{url}</loc></url>" for url in urls) + "</urlset>")

    def test_valid_sitemap(self):
        check.validate_sitemap(self.sitemap(), headers("application/xml"), urls=[URL])

    def test_bad_sitemap_namespace_duplicates_and_missing_url(self):
        cases = [(self.sitemap().replace(check.SITEMAP_NS, "https://wrong.example/"), "namespace"),
                 (self.sitemap((URL, URL)), "duplicate"), (self.sitemap(()), "missing"),
                 (self.sitemap((URL, "https://destiny.by/404.html")), "extra"), ("<urlset>", "invalid")]
        for body, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(check.CheckFailure, message):
                    check.validate_sitemap(body, headers("application/xml"), urls=[URL])

    def test_valid_robots_allows_major_bots(self):
        check.validate_robots("User-agent: *\nAllow: /\nSitemap: https://destiny.by/sitemap.xml\n",
                              headers("text/plain"), urls=[URL], bots=check.BOT_TOKENS,
                              sitemap_url="https://destiny.by/sitemap.xml")

    def test_specific_bot_block_overrides_wildcard_allow(self):
        body = ("User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /\n"
                "Sitemap: https://destiny.by/sitemap.xml\n")
        with self.assertRaisesRegex(check.CheckFailure, "disallows GPTBot"):
            check.validate_robots(body, headers("text/plain"), urls=[URL], bots=check.BOT_TOKENS,
                                  sitemap_url="https://destiny.by/sitemap.xml")

    def test_missing_robots_sitemap(self):
        with self.assertRaisesRegex(check.CheckFailure, "advertise"):
            check.validate_robots("User-agent: *\nAllow: /\n", headers("text/plain"),
                                  urls=[URL], bots=check.BOT_TOKENS, sitemap_url="https://destiny.by/sitemap.xml")

    def test_targets_include_indexable_pages_and_omit_404(self):
        configs = [{"origin": "https://destiny.by", "pages": [
            {"path": "/privacy.html", "indexable": True, **PAGE},
            {"path": "/404.html", "indexable": False, "title": "Not found"}],
            "searchBots": ["CustomSearchBot"], "userFetchers": []}]
        self.assertEqual([url for url, _ in check.targets(configs)],
                         [URL, "https://destiny.by/robots.txt", "https://destiny.by/sitemap.xml"])


if __name__ == "__main__":
    unittest.main()
