#!/usr/bin/env python3
"""Build the downloadable DESTINY example report from the report page.

Run from any directory: python3 scripts/build_report_pdf.py
Requires reportlab, fonttools and Pillow. Uses only repository assets; no network.
The result is a seven-page A4 document with embedded Cyrillic fonts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

from fontTools.ttLib import TTFont as FontToolsFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfdoc import PDFString
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets/reports/destiny-pentest-report-example.pdf"
PAGE_W, PAGE_H = A4
MARGIN = 43
WIDTH = PAGE_W - 2 * MARGIN
INK = colors.HexColor("#151619")
MUTED = colors.HexColor("#5d6068")
RED = colors.HexColor("#ec263d")
LIGHT = colors.HexColor("#f0f1f3")
LINE = colors.HexColor("#d8dadf")
PAPER = colors.HexColor("#ffffff")
CREAM = colors.HexColor("#f1efe6")
BLACK = colors.HexColor("#07090c")
TOTAL_PAGES = 7


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().translate(str.maketrans({"\u2011": "-", "\u2013": "-", "\u2014": "-", "\u2010": "-"}))


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parts: list = field(default_factory=list)

    @property
    def children(self) -> list[Node]:
        return [x for x in self.parts if isinstance(x, Node)]

    @property
    def text(self) -> str:
        return clean(" ".join(x.text if isinstance(x, Node) else x for x in self.parts))

    def all(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        result = []
        for child in self.children:
            if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get("class", "").split()):
                result.append(child)
            result.extend(child.all(tag, cls))
        return result

    def one(self, tag: str | None = None, cls: str | None = None) -> Node:
        return self.all(tag, cls)[0]

    def direct(self, tag: str) -> list[Node]:
        return [x for x in self.children if x.tag == tag]

    def by_id(self, value: str) -> Node:
        if self.attrs.get("id") == value:
            return self
        for child in self.children:
            try:
                return child.by_id(value)
            except KeyError:
                pass
        raise KeyError(value)


class ReportSource(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].parts.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def register_fonts(tmp: Path):
    for name, source, weight in [
        ("Onest", "onest-variable.ttf", 400),
        ("Onest-Semibold", "onest-variable.ttf", 600),
        ("Oswald", "oswald-variable.ttf", 500),
        ("Oswald-Bold", "oswald-variable.ttf", 600),
    ]:
        font = FontToolsFont(ROOT / "assets/fonts" / source)
        if "fvar" in font:
            font = instantiateVariableFont(font, {"wght": weight}, inplace=True)
        # ReportLab deduplicates fonts by their internal PostScript names.
        # Give each instantiated weight its own identity before embedding it.
        for record in font["name"].names:
            if record.nameID in (4, 6):
                record.string = f"Destiny-{name}".encode(record.getEncoding())
        out = tmp / f"{name}.ttf"
        font.flavor = None
        font.save(out)
        pdfmetrics.registerFont(TTFont(name, str(out)))
    pdfmetrics.registerFontFamily("Onest", normal="Onest", bold="Onest-Semibold", italic="Onest", boldItalic="Onest-Semibold")


def logo(c: canvas.Canvas, x: float, y: float, width: float, dark=False):
    """Draw the repository's simple polygonal SVG as vector paths."""
    svg = ET.fromstring((ROOT / "assets/destiny-wordmark.svg").read_text())
    scale = width / 707
    c.saveState()
    c.translate(x, y + 120 * scale)
    c.scale(scale, -scale)
    for elem in svg.iter():
        if not elem.tag.endswith("path"):
            continue
        fill = elem.attrib.get("fill", "#f1efe6")
        c.setFillColor(INK if dark and fill == "#f1efe6" else colors.HexColor(fill))
        translate = re.search(r"translate\(([-\d.]+)\)", elem.attrib.get("transform", ""))
        dx = float(translate.group(1)) if translate else 0
        tokens = re.findall(r"[MLHVZ]|-?\d+(?:\.\d+)?", elem.attrib["d"])
        path, i, cur_x, cur_y = c.beginPath(), 0, 0.0, 0.0
        while i < len(tokens):
            op = tokens[i]
            i += 1
            if op in {"M", "L"}:
                cur_x, cur_y = float(tokens[i]), float(tokens[i + 1])
                i += 2
                (path.moveTo if op == "M" else path.lineTo)(cur_x + dx, cur_y)
            elif op == "H":
                cur_x = float(tokens[i]); i += 1
                path.lineTo(cur_x + dx, cur_y)
            elif op == "V":
                cur_y = float(tokens[i]); i += 1
                path.lineTo(cur_x + dx, cur_y)
            elif op == "Z":
                path.close()
        c.drawPath(path, stroke=0, fill=1, fillMode=0)
    c.restoreState()


class Report:
    def __init__(self, output: Path, root: Node):
        self.root = root
        self.c = canvas.Canvas(str(output), pagesize=A4, pageCompression=1, invariant=1)
        self.c.setTitle("DESTINY | Пример отчёта о пентесте")
        self.c.setAuthor("DESTINY")
        self.c.setSubject("Демонстрационный отчёт: Web / API, AI / RAG, рекомендации и критерии ретеста. Все данные вымышлены.")
        self.c.setKeywords("DESTINY, пентест, образец отчёта, Web, API, AI, RAG, ретест")
        self.c.setCreator("DESTINY")
        self.c._doc.Catalog.Lang = PDFString("ru-RU")
        self.c.showOutline()
        self.page = 0
        self.y = 0
        self.styles = {
            "body": ParagraphStyle("Body", fontName="Onest", fontSize=10.1, leading=14.4, textColor=INK, spaceAfter=0, alignment=TA_LEFT, splitLongWords=False),
            "small": ParagraphStyle("Small", fontName="Onest", fontSize=8.5, leading=12.2, textColor=MUTED, splitLongWords=False),
            "label": ParagraphStyle("Label", fontName="Onest-Semibold", fontSize=8.1, leading=11.3, textColor=RED),
            "h2": ParagraphStyle("H2", fontName="Oswald", fontSize=25, leading=30, textColor=INK),
            "h3": ParagraphStyle("H3", fontName="Onest-Semibold", fontSize=12.1, leading=16, textColor=INK),
            "h4": ParagraphStyle("H4", fontName="Onest-Semibold", fontSize=10.1, leading=14.2, textColor=INK),
            "table": ParagraphStyle("Table", fontName="Onest", fontSize=8.7, leading=12.4, textColor=INK),
        }

    def p(self, text, x=None, y=None, width=None, style="body", after=9, markup=False):
        x = MARGIN if x is None else x
        y = self.y if y is None else y
        width = WIDTH if width is None else width
        para = Paragraph(text if markup else escape(clean(text)), self.styles[style])
        _, height = para.wrap(width, PAGE_H)
        if y - height < 52:
            raise ValueError(f"Page {self.page}: content exceeds safe area: {clean(text)[:90]}")
        para.drawOn(self.c, x, y - height)
        self.y = y - height - after
        return self.y

    def label(self, text):
        return self.p(text, style="label", after=9)

    def h3(self, text):
        self.y -= 3
        return self.p(text, style="h3", after=7)

    def bullets(self, items, numbered=False, small=False):
        for i, item in enumerate(items):
            top = self.y
            self.c.setFillColor(RED)
            if numbered:
                self.c.setFont("Onest-Semibold", 9.2)
                self.c.drawString(MARGIN, top - 11, f"{i + 1:02d}")
            else:
                self.c.rect(MARGIN + 1, top - 8, 3, 3, fill=1, stroke=0)
            self.p(item, x=MARGIN + (23 if numbered else 13), width=WIDTH - (23 if numbered else 13), style="small" if small else "body", after=6)

    def rule(self, gap=14):
        self.y -= 2
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(.5)
        self.c.line(MARGIN, self.y, PAGE_W - MARGIN, self.y)
        self.y -= gap

    def page_start(self, title: str, eyebrow: str, bookmark: str):
        if self.page:
            self.c.showPage()
        self.page += 1
        c = self.c
        c.setFillColor(PAPER)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        logo(c, MARGIN, PAGE_H - 47, 97, dark=True)
        c.setFillColor(MUTED)
        c.setFont("Onest", 8)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 35, "ОБРАЗЕЦ ОТЧЁТА / WEB · API · AI")
        c.setStrokeColor(LINE)
        c.setLineWidth(.5)
        c.line(MARGIN, PAGE_H - 62, PAGE_W - MARGIN, PAGE_H - 62)
        c.line(MARGIN, 42, PAGE_W - MARGIN, 42)
        c.setFont("Onest", 7.6)
        c.drawString(MARGIN, 27, "Все сценарии и данные вымышлены")
        c.drawRightString(PAGE_W - MARGIN, 27, f"DESTINY / {self.page:02d} - {TOTAL_PAGES:02d}")
        c.linkURL("https://destiny.by/report-example.html", (MARGIN, 20, MARGIN + 165, 38), relative=0, thickness=0)
        c.bookmarkPage(bookmark)
        c.addOutlineEntry(title, bookmark, level=0)
        self.y = PAGE_H - 86
        self.label(eyebrow)
        self.p(title, style="h2", after=14)

    def cover(self):
        self.page = 1
        c = self.c
        c.setFillColor(BLACK)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        img = Image.open(ROOT / "assets/visuals/blackwall-signal-v9.webp").convert("RGB")
        out = BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        c.drawImage(ImageReader(BytesIO(out.getvalue())), 0, PAGE_H - 390, width=PAGE_W, height=390, preserveAspectRatio=False, mask="auto")
        c.saveState()
        # A deliberate dark text panel, with a narrow red accent, keeps copy crisp.
        c.setFillColor(BLACK)
        c.rect(0, 0, PAGE_W, PAGE_H - 332, fill=1, stroke=0)
        c.setFillColor(RED)
        c.rect(MARGIN, PAGE_H - 358, 48, 4, fill=1, stroke=0)
        c.restoreState()
        logo(c, MARGIN, PAGE_H - 87, 204)
        c.setFillColor(CREAM)
        c.setFont("Onest-Semibold", 8.8)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 55, "SECURITY ASSESSMENT")
        c.setFont("Onest", 8.3)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 69, "WEB / API / AI")
        c.setFont("Oswald-Bold", 42)
        c.drawString(MARGIN, 433, "ПРИМЕР ОТЧЁТА")
        c.drawString(MARGIN, 378, "О ПЕНТЕСТЕ")
        white_body = ParagraphStyle("CoverText", parent=self.styles["body"], fontSize=11.2, leading=16.3, textColor=CREAM)
        text = "Сводка рисков. Доказательства. Исправления.\nКритерии повторной проверки."
        p = Paragraph(escape(text).replace("\n", "<br/>"), white_body)
        _, h = p.wrap(WIDTH, 100)
        p.drawOn(c, MARGIN, 344 - h)
        c.setFillColor(colors.HexColor("#a7a9af"))
        c.setFont("Onest", 9)
        c.drawString(MARGIN, 270, "Демонстрационный документ. Все данные вымышлены.")
        c.setStrokeColor(colors.HexColor("#33353a"))
        c.line(MARGIN, 248, PAGE_W - MARGIN, 248)
        contents = [
            ("02", "Сводка и объём проверки", "summary"),
            ("03-04", "DEMO-01 / Доступ к чужим документам", "web"),
            ("05-06", "DEMO-02 / Права AI-агента", "ai"),
            ("07", "Ретест и передача команде", "handover"),
        ]
        yy = 222
        for num, label, anchor in contents:
            c.setFillColor(RED)
            c.setFont("Onest-Semibold", 9)
            c.drawString(MARGIN, yy, num)
            c.setFillColor(CREAM)
            c.setFont("Onest", 9.5)
            c.drawString(MARGIN + 51, yy, label)
            c.linkRect("", anchor, (MARGIN, yy - 3, PAGE_W - MARGIN, yy + 13), relative=0, thickness=0)
            yy -= 28
        c.setFont("Onest", 9)
        c.setFillColor(CREAM)
        c.drawString(MARGIN, 49, "destiny.by")
        c.drawRightString(PAGE_W - MARGIN, 49, "security@destiny.by")
        c.linkURL("https://destiny.by/", (MARGIN, 45, MARGIN + 70, 62), thickness=0)
        c.linkURL("mailto:security@destiny.by", (PAGE_W - MARGIN - 120, 45, PAGE_W - MARGIN, 62), thickness=0)
        c.bookmarkPage("cover")
        c.addOutlineEntry("Пример отчёта о пентесте", "cover", level=0)

    def summary(self):
        self.page_start("Сводка и объём проверки", "01 / РИСКИ И ГРАНИЦЫ", "summary")
        summary = self.root.by_id("executive-summary")
        self.p(summary.one(cls="doc-section-header").direct("p")[-1].text)
        for article in summary.one(cls="doc-executive").direct("article"):
            self.label(article.one(cls="doc-label").text)
            self.p(article.one("h3").text, style="h3", after=5)
            self.p(article.direct("p")[-1].text, after=13)
        self.p(summary.one(cls="doc-priority-note").text, style="small", after=14)
        self.rule(12)
        coverage = self.root.by_id("coverage")
        self.h3("Объём и ограничения")
        self.p(coverage.one(cls="doc-section-header").direct("p")[-1].text, after=10)
        rows = []
        for tr in coverage.one("table").all("tr"):
            cells = tr.direct("th") or tr.direct("td")
            rows.append([Paragraph(escape(c.text), self.styles["table"]) for c in cells])
        table = Table(rows, colWidths=[93, 200, WIDTH - 293], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), .4, LINE),
        ]))
        _, h = table.wrap(WIDTH, PAGE_H)
        if self.y - h < 85:
            raise ValueError("Summary table does not fit")
        table.drawOn(self.c, MARGIN, self.y - h)
        self.y -= h + 11
        self.p(coverage.one(cls="doc-caption").text, style="small", after=0)

    def metadata(self, finding: Node):
        cells = finding.one(cls="doc-metadata").direct("div")
        top = self.y
        col = WIDTH / 3
        bottoms = []
        for i, cell in enumerate(cells):
            x = MARGIN + i * col
            self.p(cell.one("dt").text, x=x, y=top, width=col - 12, style="small", after=4)
            self.p(cell.one("dd").text, x=x, width=col - 12, style="h4", after=0)
            bottoms.append(self.y)
        self.y = min(bottoms) - 12
        self.rule(12)

    def boundary(self, finding: Node):
        labels = [n.text for n in finding.one(cls="doc-boundary").direct("span")]
        gap, col = 19, (WIDTH - 38) / 3
        top = self.y
        self.c.setFillColor(LIGHT)
        for i, label in enumerate(labels):
            x = MARGIN + i * (col + gap)
            self.c.rect(x, top - 48, col, 48, fill=1, stroke=0)
            self.p(label, x=x + 10, y=top - 9, width=col - 20, style="h4", after=0)
            if i < 2:
                self.c.setStrokeColor(RED)
                self.c.setLineWidth(1)
                ax, ay = x + col + 5, top - 24
                self.c.line(ax, ay, ax + 9, ay)
                self.c.line(ax + 9, ay, ax + 6, ay + 3)
                self.c.line(ax + 9, ay, ax + 6, ay - 3)
        self.y = top - 61

    def evidence(self, finding: Node):
        section = finding.by_id("web-evidence-title" if finding.attrs["id"] == "web-example" else "ai-evidence-title")
        # Find the section by its heading's parent through the named outer section.
        section = next(n for n in finding.all("section") if section in n.children)
        self.h3(section.one("h3").text)
        pairs = section.one("dl").children
        for i in range(0, len(pairs), 2):
            top = self.y
            self.p(pairs[i].text, width=125, style="small", after=0)
            self.p(pairs[i + 1].text, x=MARGIN + 132, y=top, width=WIDTH - 132, after=5)
        self.y -= 5
        obs = section.one(cls="doc-observation")
        for item in obs.direct("p"):
            strong = item.one("strong").text
            rest = clean(item.text[len(strong):])
            self.p(f"<b>{escape(strong)}.</b> {escape(rest)}", markup=True, after=7)
        self.p(section.one(cls="doc-caption").text, style="small", after=0)

    def finding(self, ident: str):
        is_web = ident == "web"
        finding = self.root.by_id("web-example" if is_web else "ai-example")
        title = finding.one("h2").text.rstrip(".")
        self.page_start(title, "02 / DEMO-01 · WEB / API" if is_web else "03 / DEMO-02 · AI / RAG", ident)
        self.p(finding.one(cls="doc-finding-head").direct("p")[0].text)
        self.metadata(finding)
        impact = finding.by_id(f"{ident}-impact-title")
        impact_section = next(n for n in finding.all("section") if impact in n.children)
        self.h3(impact.text)
        self.p(impact_section.direct("p")[0].text, after=13)
        self.h3("Путь через границу доверия")
        self.boundary(finding)
        if is_web:
            for col in finding.one(cls="doc-two-columns").direct("div"):
                self.p(col.one("h4").text, style="h4", after=5)
                self.bullets([x.text for x in col.one("ul").direct("li")], small=True)
                self.y -= 3
        else:
            context = finding.by_id("ai-context-title")
            context_section = next(n for n in finding.all("section") if context in n.children)
            self.p(context_section.direct("p")[0].text, after=13)
        self.evidence(finding)

    def actions(self, ident: str):
        is_web = ident == "web"
        finding = self.root.by_id("web-example" if is_web else "ai-example")
        self.page_start("Исправление и проверка результата", "DEMO-01 / WEB · API" if is_web else "DEMO-02 / AI · RAG", f"{ident}-actions")
        self.h3("Логика воспроизведения" if is_web else "Условия воспроизведения")
        self.bullets([x.text for x in finding.one("ol").direct("li")], numbered=True)
        if not is_web:
            section = next(n for n in finding.all("section") if n.attrs.get("aria-labelledby") == "ai-reproduction-title")
            self.p(section.one(cls="doc-caption").text, style="small", after=10)
        self.rule(12)
        self.h3("План исправления")
        for li in finding.one(cls="doc-checklist").direct("li"):
            self.p(li.one("strong").text, style="h4", after=4)
            self.p(li.one("span").text, after=12)
        self.rule(12)
        self.h3("Критерии ретеста")
        section = next(n for n in finding.all("section") if n.attrs.get("aria-labelledby") == f"{ident}-retest-title")
        self.bullets([x.text for x in section.one("ul").direct("li")])

    def handover(self):
        self.page_start("Ретест и передача команде", "04 / РЕЗУЛЬТАТ РАБОТЫ", "handover")
        section = self.root.by_id("retest-statuses")
        self.p(section.one(cls="doc-section-header").direct("p")[-1].text, after=11)
        for row in section.one(cls="doc-statuses").direct("div"):
            self.p(row.one("dt").text, style="h4", after=4)
            self.p(row.one("dd").text, after=11)
        self.rule(12)
        self.h3("Что получает команда")
        for i, li in enumerate(self.root.by_id("handover").one("ol").direct("li")):
            self.p(f"{i + 1:02d} / {li.one('h3').text}", style="h4", after=4)
            self.p(li.one("p").text, after=11)
        self.rule(12)
        self.h3("Обсудить проверку")
        self.p("Опишите продукт и критичные операции. Определим объём проверки и результат, который нужен вашей команде.", after=9)
        self.p('<link href="mailto:security@destiny.by" color="#b31c30">security@destiny.by</link>  /  <link href="https://www.linkedin.com/in/vadimohka/" color="#b31c30">LinkedIn</link>  /  <link href="https://destiny.by/" color="#b31c30">destiny.by</link>', markup=True, after=0)

    def build(self):
        self.cover()
        self.summary()
        self.finding("web")
        self.actions("web")
        self.finding("ai")
        self.actions("ai")
        self.handover()
        if self.page != TOTAL_PAGES:
            raise ValueError("Unexpected page count")
        self.c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = ReportSource((ROOT / "report-example.html").read_text(encoding="utf-8")).root
    with tempfile.TemporaryDirectory(prefix="destiny-report-fonts-") as tmp:
        register_fonts(Path(tmp))
        Report(args.output, source).build()
    print(f"Built {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}: {TOTAL_PAGES} pages, {args.output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
