"""Regression checks for the site's public indexing policy and hidden exclusions."""

import unittest

from build_discovery import ROOT, Document, build, load_configs, shared_discovery, validate


class DiscoveryIndexingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = load_configs()
        cls.outputs, cls.graphs = {}, []
        for config in cls.configs:
            sources = {page["file"]: (ROOT / page["file"]).read_text(encoding="utf-8")
                       for page in config["pages"]}
            documents = {name: Document(source) for name, source in sources.items()}
            output, graphs = build(config, sources, documents, cls.configs)
            cls.outputs.update(output)
            cls.graphs.append(graphs)
        cls.outputs.update(shared_discovery(cls.configs))

    def test_all_public_content_is_indexable_and_error_pages_are_excluded(self):
        for config in self.configs:
            for page in config["pages"]:
                with self.subTest(file=page["file"]):
                    self.assertEqual(page["indexable"], page["kind"] != "error")
                    if page["indexable"]:
                        self.assertTrue(page.get("markdown"), "Public content needs its text mirror")

    def test_bot_specific_noindex_cannot_override_public_robots(self):
        for bot, value in (("GoogleBot", "NoIndex, follow"), ("bingbot", "NONE"),
                           ("OAI-SearchBot", "noindex")):
            with self.subTest(bot=bot):
                outputs = dict(self.outputs)
                outputs["index.html"] = outputs["index.html"].replace(
                    "</head>", f'<meta name="{bot}" content="{value}"></head>')
                with self.assertRaisesRegex(ValueError, "meta tag blocks indexing"):
                    validate(self.configs[0], outputs, self.graphs[0], self.configs)

    def test_canonical_page_cannot_redirect_using_meta_refresh(self):
        outputs = dict(self.outputs)
        outputs["index.html"] = outputs["index.html"].replace(
            "</head>", '<meta http-equiv="Refresh" content="0; url=/contact.html"></head>')
        with self.assertRaisesRegex(ValueError, "must not use meta refresh"):
            validate(self.configs[0], outputs, self.graphs[0], self.configs)


if __name__ == "__main__":
    unittest.main()
