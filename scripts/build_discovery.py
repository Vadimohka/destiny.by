#!/usr/bin/env python3
"""Build search metadata and public text/JSON-LD views from the same HTML content.

Run without arguments to refresh outputs. --check is read-only and fails on drift
or invalid source links/metadata. Uses the Python standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- BEGIN GENERATED DISCOVERY -->"
END = "<!-- END GENERATED DISCOVERY -->"
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
OMIT = set("script style svg nav form button input select textarea label noscript template".split())


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def has_class(self, value: str) -> bool:
        return value in (self.attrs.get("class") or "").split()

    def all(self, tag: str | None = None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.all(tag)

    def first(self, *, tag=None, ident=None, css_class=None):
        return next((n for n in self.all(tag)
                     if (ident is None or n.attrs.get("id") == ident)
                     and (css_class is None or n.has_class(css_class))), None)


class Document(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def plain(node: Node | str | None) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return compact(node)
    if node.tag in {"script", "style", "svg"} or node.attrs.get("aria-hidden") == "true":
        return ""
    return compact(" ".join(plain(child) for child in node.children))


def required(node, message):
    if node is None:
        raise ValueError(message)
    return node


def absolute(config, value):
    return urljoin(config["origin"] + "/", value)


def page_url(config, page):
    return absolute(config, page["path"])


def ref(value):
    return {"@id": value}


def robots_value(page):
    if page["indexable"]:
        return "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
    return "noindex, follow"


def source_facts(config, documents):
    home = documents["index.html"].root
    hero = plain(required(home.first(css_class="hero-description"), "Missing hero description"))
    services = []
    for item in config["services"]:
        source = documents[item["file"]].root
        panel = required(source.first(ident=item["id"]), f"Missing service {item['id']} in {item['file']}")
        intro = plain(required(panel.first(css_class="service-intro"), "Missing service introduction"))
        outcome = required(panel.first(css_class="service-outcome"), "Missing service outcome")
        result = plain(required(outcome.first(tag="p"), "Missing service outcome text"))
        services.append({**item, "description": intro, "output": result})
    faq_source = config["faq"]
    faq_section = required(documents[faq_source["file"]].root.first(ident=faq_source["sectionId"]),
                           "Missing FAQ section")
    faqs = []
    for detail in faq_section.all("details"):
        summary = required(detail.first(tag="summary"), "FAQ has no question")
        question = plain(summary)
        answer = compact(" ".join(plain(n) for n in detail.children if n is not summary))
        ident = detail.attrs.get("id")
        if not ident or not question or not answer:
            raise ValueError("Every FAQ needs a stable id, question and answer")
        faqs.append({"id": ident, "question": question, "answer": answer})
    if not faqs or len({f["id"] for f in faqs}) != len(faqs):
        raise ValueError("FAQ ids must be unique")
    return hero, services, faqs


def graphs(config, documents):
    origin = config["origin"] + "/"
    hero, services, faqs = source_facts(config, documents)
    org_id, site_id, logo_id = (origin + fragment for fragment in ("#organization", "#website", "#logo"))
    organization = {"@type": "Organization", "@id": org_id, "name": config["name"],
                    "url": origin, "description": hero, "logo": ref(logo_id)}
    if config.get("email"):
        organization["email"] = config["email"]
    common = [
        organization,
        {"@type": "ImageObject", "@id": logo_id, "url": absolute(config, config["logo"]["path"]),
         "contentUrl": absolute(config, config["logo"]["path"]),
         "width": config["logo"]["width"], "height": config["logo"]["height"],
         "caption": config["name"]},
        {"@type": "WebSite", "@id": site_id, "url": origin, "name": config["name"],
         "publisher": ref(org_id), "inLanguage": config["language"]},
    ]
    # Keep established service identities while changing their public page URLs.
    service_nodes = {
        item["id"]: {"@type": "Service", "@id": origin + "#" + item["id"] + "-entity",
                     "name": item["name"], "serviceType": item["serviceType"],
                     "description": item["description"], "provider": ref(org_id),
                     "url": absolute(config, item["path"]),
                     "serviceOutput": {"@type": "Thing", "description": item["output"]}}
        for item in services
    }
    faq_page = next(p for p in config["pages"] if p["file"] == config["faq"]["file"])
    faq_url = page_url(config, faq_page)
    result = {}
    for page in config["pages"]:
        if page["kind"] == "error":
            result[page["file"]] = []
            continue
        url = page_url(config, page)
        webpage = {"@type": "ContactPage" if page["kind"] == "contact" else "WebPage",
                   "@id": url + "#webpage", "url": url,
                   "name": page["title"], "description": page["description"],
                   "inLanguage": config["language"], "isPartOf": ref(site_id),
                   "publisher": ref(org_id), "dateModified": page["updated"]}
        nodes = [*common, webpage]
        if page["kind"] == "home":
            catalog_id = origin + "#service-catalog"
            webpage["mainEntity"] = ref(catalog_id)
            nodes.append({"@type": "OfferCatalog", "@id": catalog_id,
                          "name": "Услуги DESTINY", "url": origin + "#services",
                          "itemListElement": [{"@type": "Offer", "itemOffered": ref(service["@id"])}
                                              for service in service_nodes.values()]})
            nodes.extend(service_nodes.values())
        else:
            breadcrumb = required(documents[page["file"]].root.first(css_class="page-breadcrumb"),
                                  f"Missing visible breadcrumbs in {page['file']}")
            current = required(next((n for n in breadcrumb.all() if n.attrs.get("aria-current") == "page"), None),
                               "Missing current breadcrumb")
            first = required(breadcrumb.first(tag="a"), "Missing home breadcrumb")
            breadcrumb_id = url + "#breadcrumb"
            webpage["breadcrumb"] = ref(breadcrumb_id)
            nodes.append({"@type": "BreadcrumbList", "@id": breadcrumb_id, "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": plain(first), "item": origin},
                {"@type": "ListItem", "position": 2, "name": plain(current), "item": url}]})
            if page["kind"] == "service":
                service = service_nodes[page["serviceId"]]
                webpage["mainEntity"] = ref(service["@id"])
                nodes.append(service)
            elif page["file"] == config["faq"]["file"]:
                faq_id = faq_url + "#faq"
                webpage["hasPart"] = ref(faq_id)
                nodes.append({"@type": "FAQPage", "@id": faq_id,
                              "url": faq_url + "#" + config["faq"]["sectionId"],
                              "name": "Вопросы о пентесте", "isPartOf": ref(webpage["@id"]),
                              "inLanguage": config["language"], "mainEntity": [
                                  {"@type": "Question", "@id": faq_url + "#" + f["id"],
                                   "url": faq_url + "#" + f["id"], "name": f["question"],
                                   "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
                                  for f in faqs]})
            elif page["kind"] == "contact":
                webpage["mainEntity"] = ref(org_id)
                if config.get("linkedin"):
                    webpage["relatedLink"] = config["linkedin"]
            elif page["kind"] == "report":
                demo = plain(required(documents[page["file"]].root.first(css_class="doc-demo-note"),
                                      "Report must contain its visible demo notice"))
                if not any(word in demo.lower() for word in ("вымышлен", "демонстрацион")):
                    raise ValueError("The example report must remain explicitly a demonstration")
                report_id = url + "#report"
                webpage["mainEntity"] = ref(report_id)
                report = {"@type": "Report", "@id": report_id, "url": url,
                          "name": "Демонстрационный отчёт о пентесте Web/API и AI",
                          "description": demo, "inLanguage": config["language"],
                          "publisher": ref(org_id), "mainEntityOfPage": ref(webpage["@id"]),
                          "dateModified": page["updated"]}
                if page.get("pdf"):
                    report["encoding"] = {"@type": "MediaObject", "encodingFormat": "application/pdf",
                                          "contentUrl": absolute(config, page["pdf"]),
                                          "inLanguage": config["language"]}
                nodes.append(report)
        result[page["file"]] = nodes
    return result


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def generated_head(config, page, graph):
    esc = lambda value: html.escape(str(value), quote=True)
    tags = [BEGIN, f'<title>{esc(page["title"])}</title>',
            f'<meta name="description" content="{esc(page["description"])}">',
            f'<meta name="robots" content="{robots_value(page)}">']
    if page["kind"] != "error":
        tags.append(f'<link rel="canonical" href="{page_url(config, page)}">')
    meta = [("property", "og:type", "website"), ("property", "og:locale", config["locale"]),
            ("property", "og:site_name", config["name"]), ("property", "og:title", page["title"]),
            ("property", "og:description", page["description"])]
    if page["kind"] != "error":
        meta.append(("property", "og:url", page_url(config, page)))
    social = config["socialImage"]
    meta.extend([( "property", "og:image", absolute(config, social["path"])),
                 ("property", "og:image:type", "image/png"),
                 ("property", "og:image:width", social["width"]),
                 ("property", "og:image:height", social["height"]),
                 ("property", "og:image:alt", social["alt"]),
                 ("name", "twitter:card", "summary_large_image"),
                 ("name", "twitter:title", page["title"]),
                 ("name", "twitter:description", page["description"]),
                 ("name", "twitter:image", absolute(config, social["path"])),
                 ("name", "twitter:image:alt", social["alt"])])
    tags.extend(f'<meta {attr}="{key}" content="{esc(value)}">' for attr, key, value in meta)
    tags.extend([
        f'<link rel="describedby" href="{absolute(config, "llms.txt")}" type="text/plain">',
        f'<link rel="describedby" href="{absolute(config, "ai.json")}" type="application/ld+json">',
        f'<link rel="sitemap" href="{absolute(config, "sitemap.xml")}" type="application/xml">',
    ])
    if page.get("markdown"):
        tags.append(f'<link rel="alternate" href="{absolute(config, page["markdown"])}" type="text/markdown" title="{esc(page["title"])}">')
    if page.get("pdf"):
        tags.append(f'<link rel="alternate" href="{absolute(config, page["pdf"])}" type="application/pdf" title="Пример отчёта в PDF">')
    if graph:
        encoded = json_text({"@context": "https://schema.org", "@graph": graph}).rstrip().replace("<", "\\u003c")
        tags.extend(['<script type="application/ld+json">', encoded, '</script>'])
    tags.append(END)
    return "\n".join("  " + line for tag in tags for line in tag.splitlines()) + "\n"


def replace_head(source, content):
    match = required(re.search(r"(<head\b[^>]*>)(.*?)(</head>)", source, re.S | re.I), "Missing head")
    head = match.group(2)
    head = re.sub(r"[ \t]*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", head, flags=re.S)
    head = re.sub(r"[ \t]*<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>.*?</script>\s*", "", head, flags=re.S | re.I)
    head = re.sub(r"^[ \t]*<title>.*?</title>\s*\n?", "", head, flags=re.M | re.I)
    lines = []
    for line in head.splitlines():
        if re.search(r'<meta\s[^>]*(?:name=[\"\'](?:description|robots|twitter:[^\"\']+)|property=[\"\']og:[^\"\']+)', line, re.I):
            continue
        if re.search(r'<link\s[^>]*rel=[\"\'](?:canonical|describedby|sitemap|alternate)[\"\']', line, re.I):
            continue
        if line.strip():
            lines.append(line)
    position = next((i for i, line in enumerate(lines) if re.search(r'<(?:link|script)\b', line)), len(lines))
    lines[position:position] = content.rstrip().splitlines()
    updated = match.group(1) + "\n" + "\n".join(lines) + "\n" + match.group(3)
    return source[:match.start()] + updated + source[match.end():]


def omitted(node):
    destination = urlsplit(node.attrs.get("href") or "")
    direct_contact = (destination.scheme == "mailto"
                      or destination.hostname == "www.linkedin.com")
    pdf_download = "download" in node.attrs and destination.path.lower().endswith(".pdf")
    return (node.tag in OMIT or "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true"
            or node.has_class("doc-print-heading") or node.has_class("sr-only")
            or (node.tag == "a" and ((node.has_class("button") and not (direct_contact or pdf_download))
                                    or "data-service" in node.attrs or "data-format" in node.attrs)))


def inline(node, base):
    if isinstance(node, str):
        return node
    if omitted(node):
        return ""
    if node.tag == "br":
        return " "
    if node.tag == "img":
        return ""
    value = "".join(inline(child, base) for child in node.children)
    value = compact(value)
    if not value:
        return ""
    if node.tag in {"strong", "b"}:
        return " **" + value + "** "
    if node.tag in {"em", "i"}:
        return " *" + value + "* "
    if node.tag == "code":
        return " `" + value + "` "
    if node.tag == "a" and node.attrs.get("href"):
        return "[" + value + "](" + urljoin(base, node.attrs["href"]) + ")"
    if node.tag in {"span", "small", "dt", "dd", "p", "div", "section", "li"} or re.fullmatch(r"h[1-6]", node.tag):
        return " " + value + " "
    return value


def markdown_node(node, base):
    if isinstance(node, str):
        return compact(node)
    if omitted(node):
        return ""
    if node.tag == "img":
        return ""
    if node.has_class("doc-boundary") and node.attrs.get("aria-label"):
        return "\n\n" + node.attrs["aria-label"] + ".\n\n"
    if node.tag == "table":
        rows = [[compact(inline(cell, base)).replace("|", "\\|") for cell in row.children
                 if isinstance(cell, Node) and cell.tag in {"th", "td"}] for row in node.all("tr")]
        rows = [row for row in rows if row]
        if not rows:
            return ""
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("Markdown mirror requires rectangular tables")
        rendered = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        rendered += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n\n" + "\n".join(rendered) + "\n\n"
    if node.tag == "dl":
        groups, term = [], None
        for item in node.all():
            if item.tag == "dt":
                term = compact(inline(item, base))
            elif item.tag == "dd":
                description = compact(inline(item, base))
                groups.append("- " + ("**" + term + ":** " if term else "") + description)
        return "\n\n" + "\n".join(groups) + "\n\n"
    if node.tag in {"ul", "ol"}:
        items = [c for c in node.children if isinstance(c, Node) and c.tag == "li"]
        lines = []
        for position, item in enumerate(items, 1):
            value = compact(inline(item, base))
            if node.tag == "ol":
                value = re.sub(r"^0?" + str(position) + r"\s+", "", value)
            if value:
                lines.append((str(position) + ". " if node.tag == "ol" else "- ") + value)
        return "\n\n" + "\n".join(lines) + "\n\n"
    if re.fullmatch(r"h[1-6]", node.tag) or node.tag == "summary":
        level = max(2, int(node.tag[1])) if node.tag != "summary" else 3
        return "\n\n" + "#" * level + " " + compact(inline(node, base)) + "\n\n"
    if node.tag == "p":
        value = compact(inline(node, base))
        return "\n\n" + value + "\n\n" if value else ""
    if node.tag == "a":
        value = inline(node, base)
        return "\n\n" + value + "\n\n" if value else ""
    if node.tag in {"strong", "b", "em", "i", "span", "small", "code", "time"}:
        return inline(node, base)
    value = "".join(markdown_node(child, base) for child in node.children)
    return "\n\n" + value + "\n\n" if value.strip() else ""


def markdown_page(config, page, document):
    main = required(document.root.first(tag="main"), f"Missing main in {page['file']}")
    url = page_url(config, page)
    value = markdown_node(main, url)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return (f"# {page['title']}\n\nИсточник: [{url}]({url})\n\n"
            f"Обновлено: {page['updated']}\n\n{value}\n")


def build(config, sources, documents):
    output, page_graphs = {}, graphs(config, documents)
    for page in config["pages"]:
        output[page["file"]] = replace_head(sources[page["file"]], generated_head(config, page, page_graphs[page["file"]]))
        if page.get("markdown"):
            output[page["markdown"]] = markdown_page(config, page, documents[page["file"]])
    combined = {}
    for graph in page_graphs.values():
        for node in graph:
            if node["@id"] in combined and combined[node["@id"]] != node:
                raise ValueError(f"Conflicting graph identity {node['@id']}")
            combined[node["@id"]] = node
    output["ai.json"] = json_text({"@context": "https://schema.org", "@id": absolute(config, "ai.json"), "@graph": list(combined.values())})
    hero, _, _ = source_facts(config, documents)
    mirrored = [p for p in config["pages"] if p.get("markdown")]
    llms = [f"# {config['name']}", "", f"> {hero}", "", "## Содержание", ""]
    for page in mirrored:
        llms.append(f"- [{page['title']}]({absolute(config, page['markdown'])}): {page['description']}")
    llms += ["", "## Машиночитаемые представления", "",
             f"- [JSON-LD]({absolute(config, 'ai.json')}): сущности сайта и услуг в Schema.org.",
             f"- [Полный текст]({absolute(config, 'llms-full.txt')}): текстовые версии услуг, подхода, контактов и демонстрационного отчёта.",
             "", "## Optional", ""]
    llms += [f"- [{page['title']}]({page_url(config, page)}): исходная HTML-страница." for page in config["pages"] if page["kind"] != "error"]
    output["llms.txt"] = "\n".join(llms) + "\n"
    output["llms-full.txt"] = "\n\n---\n\n".join(output[p["markdown"]].rstrip() for p in mirrored) + "\n"
    lines = ["User-agent: *", "Allow: /", ""]
    for bot in config["searchBots"] + config["userFetchers"]:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    output["robots.txt"] = "\n".join(lines + [f"Sitemap: {absolute(config, 'sitemap.xml')}", ""])
    entries = [f"  <url>\n    <loc>{html.escape(page_url(config, page))}</loc>\n    <lastmod>{page['updated']}</lastmod>\n  </url>"
               for page in config["pages"] if page["indexable"]]
    output["sitemap.xml"] = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(entries) + "\n</urlset>\n"
    return output, page_graphs


def validate(config, outputs, page_graphs):
    errors = []
    origin = urlsplit(config["origin"])
    if origin.scheme != "https" or not origin.netloc or origin.path or origin.query or origin.fragment:
        errors.append("origin must be an HTTPS origin without a trailing slash or path")
    pages_by_file = {page["file"]: page for page in config["pages"]}
    if len(pages_by_file) != len(config["pages"]):
        errors.append("Configured page filenames must be unique")
    if len({page["path"] for page in config["pages"]}) != len(config["pages"]):
        errors.append("Configured canonical paths must be unique")
    for page in config["pages"]:
        if Path(page["file"]).name != page["file"]:
            errors.append("Public page files must be root-level filenames")
        if page.get("markdown") and Path(page["markdown"]).name != page["markdown"]:
            errors.append("Markdown mirrors must be root-level filenames")
    for service in config["services"]:
        page = pages_by_file.get(service["file"])
        if not page or page["path"] != service["path"] or page.get("serviceId") != service["id"]:
            errors.append(f"Service identity and canonical page disagree: {service['id']}")
    if config.get("email"):
        contact_page = next(p for p in config["pages"] if p["kind"] == "contact")
        if "mailto:" + config["email"] not in outputs[contact_page["file"]]:
            errors.append("Organization email must be published as a real contact link")
    if config.get("linkedin"):
        contact_page = next(p for p in config["pages"] if p["kind"] == "contact")
        if config["linkedin"] not in outputs[contact_page["file"]]:
            errors.append("Contact profile must be published as a real contact link")
    logo = ET.fromstring((ROOT / config["logo"]["path"]).read_text(encoding="utf-8"))
    viewbox = [float(value) for value in logo.attrib.get("viewBox", "").split()]
    if len(viewbox) != 4 or viewbox[2:] != [config["logo"]["width"], config["logo"]["height"]]:
        errors.append("Logo dimensions differ from its SVG viewBox")
    image = (ROOT / config["socialImage"]["path"]).read_bytes()
    if image[:8] != b"\x89PNG\r\n\x1a\n" or len(image) < 24:
        errors.append("Social image must be the declared PNG")
    elif struct.unpack(">II", image[16:24]) != (config["socialImage"]["width"], config["socialImage"]["height"]):
        errors.append("Social image dimensions differ from the PNG")
    documents = {p["file"]: Document(outputs[p["file"]]) for p in config["pages"]}
    all_ids = {}
    for filename, document in documents.items():
        ids = [n.attrs["id"] for n in document.root.all() if n.attrs.get("id")]
        all_ids[filename] = set(ids)
        if len(ids) != len(set(ids)):
            errors.append(f"{filename}: duplicate HTML ids")
        if len(list(document.root.all("h1"))) != 1:
            errors.append(f"{filename}: expected one H1")

    def local_url(value, base, label):
        resolved = urlsplit(urljoin(base, value))
        if resolved.scheme not in {"http", "https"} or resolved.netloc != urlsplit(config["origin"]).netloc:
            return
        if resolved.scheme != "https":
            errors.append(f"{label}: local URL is not HTTPS: {value}")
        path = unquote(resolved.path).lstrip("/") or "index.html"
        if path.endswith("/"):
            path += "index.html"
        if path not in outputs and not (ROOT / path).is_file():
            errors.append(f"{label}: missing local target {path}")
        fragment = unquote(resolved.fragment)
        if fragment and path in all_ids and fragment not in all_ids[path]:
            errors.append(f"{label}: missing fragment {path}#{fragment}")

    def check_graph(graph, label):
        ids, references = {}, []
        def walk(value):
            if isinstance(value, dict):
                if "@id" in value:
                    if set(value) == {"@id"}:
                        references.append(value["@id"])
                    else:
                        ident = value["@id"]
                        if ident in ids and ids[ident] != value:
                            errors.append(f"{label}: conflicting graph node {ident}")
                        ids[ident] = value
                for key, child in value.items():
                    if key in {"url", "contentUrl", "item"} and isinstance(child, str):
                        local_url(child, config["origin"] + "/", label)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(graph)
        for ident in references:
            if ident not in ids:
                errors.append(f"{label}: dangling JSON-LD reference {ident}")

    for page in config["pages"]:
        filename, url = page["file"], page_url(config, page)
        document = documents[filename].root
        titles = list(document.all("title"))
        if len(titles) != 1 or plain(titles[0]) != page["title"]:
            errors.append(f"{filename}: invalid title")
        metadata = list(document.all("meta"))
        for key, expected in (("description", page["description"]), ("robots", robots_value(page))):
            matches = [n for n in metadata if n.attrs.get("name") == key]
            if len(matches) != 1 or matches[0].attrs.get("content") != expected:
                errors.append(f"{filename}: invalid {key}")
        canonical = [n.attrs.get("href") for n in document.all("link") if n.attrs.get("rel") == "canonical"]
        if canonical != ([] if page["kind"] == "error" else [url]):
            errors.append(f"{filename}: invalid canonical")
        for node in document.all():
            for attr in ("href", "src"):
                if node.attrs.get(attr):
                    local_url(node.attrs[attr], url, filename)
        scripts = [n for n in document.all("script") if n.attrs.get("type") == "application/ld+json"]
        if len(scripts) != bool(page_graphs[filename]):
            errors.append(f"{filename}: unexpected JSON-LD count")
        for node in scripts:
            parsed = json.loads("".join(c for c in node.children if isinstance(c, str)))
            if parsed.get("@graph") != page_graphs[filename]:
                errors.append(f"{filename}: JSON-LD differs from source")
            check_graph(parsed["@graph"], filename)
        if page.get("markdown"):
            mirror = outputs[page["markdown"]]
            for match in re.finditer(r"\]\((https?://[^\s)]+)\)", mirror):
                local_url(match.group(1), url, page["markdown"])
    check_graph(json.loads(outputs["ai.json"])["@graph"], "ai.json")
    source_faqs = source_facts(config, documents)[2]
    faq_file = config["faq"]["file"]
    faq_page = next(p for p in config["pages"] if p["file"] == faq_file)
    faq_mirror = outputs[faq_page["markdown"]]
    faq_node = next(n for n in page_graphs[faq_file] if n["@type"] == "FAQPage")
    extracted = [(q["name"], q["acceptedAnswer"]["text"]) for q in faq_node["mainEntity"]]
    if extracted != [(f["question"], f["answer"]) for f in source_faqs]:
        errors.append("FAQ graph is not an exact representation of visible FAQ")
    for f in source_faqs:
        if f["question"] not in faq_mirror or f["answer"] not in compact(faq_mirror):
            errors.append(f"FAQ missing from text mirror: {f['id']}")
    report_page = next(p for p in config["pages"] if p["kind"] == "report")
    report_notice = plain(documents[report_page["file"]].root.first(css_class="doc-demo-note"))
    report_text = compact(re.sub(r"\*+", "", outputs[report_page["markdown"]]))
    if not report_notice or report_notice not in report_text:
        errors.append("Report mirror lost its visible demonstration notice")
    if report_page.get("pdf"):
        pdf_url = absolute(config, report_page["pdf"])
        report_body = documents[report_page["file"]].root.first(tag="body")
        if not any(absolute(config, node.attrs.get("href") or "") == pdf_url
                   and "hidden" not in node.attrs for node in report_body.all("a")):
            errors.append("Report PDF must have a visible download link")
        if pdf_url not in outputs[report_page["markdown"]]:
            errors.append("Report PDF is missing from its text mirror")
    sitemap = ET.fromstring(outputs["sitemap.xml"])
    locations = [n.text for n in sitemap.findall("{*}url/{*}loc")]
    expected = [page_url(config, p) for p in config["pages"] if p["indexable"]]
    if locations != expected:
        errors.append("Sitemap must contain exactly the canonical indexable pages")
    if len({p["title"] for p in config["pages"]}) != len(config["pages"]):
        errors.append("Page titles must be unique")
    for page in config["pages"]:
        if page.get("updated"):
            date.fromisoformat(page["updated"])
    if errors:
        raise ValueError("\n".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate generated files without writing")
    args = parser.parse_args()
    try:
        config = json.loads((ROOT / "seo/site.json").read_text(encoding="utf-8"))
        sources = {p["file"]: (ROOT / p["file"]).read_text(encoding="utf-8") for p in config["pages"]}
        documents = {filename: Document(content) for filename, content in sources.items()}
        outputs, page_graphs = build(config, sources, documents)
        validate(config, outputs, page_graphs)
        drift = [filename for filename, content in outputs.items()
                 if not (ROOT / filename).is_file() or (ROOT / filename).read_text(encoding="utf-8") != content]
        if args.check and drift:
            raise ValueError("Generated discovery files are stale: " + ", ".join(drift) + ". Run python scripts/build_discovery.py.")
        if not args.check:
            for filename in drift:
                (ROOT / filename).write_text(outputs[filename], encoding="utf-8")
        print(f"Discovery {'verified' if args.check else 'generated'}: {len(outputs)} files, "
              f"{len(config['services'])} services, {len(source_facts(config, documents)[2])} FAQ answers.")
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"Discovery validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
