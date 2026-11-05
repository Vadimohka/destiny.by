#!/usr/bin/env python3
"""Build the Belarusian social card from the original vector artwork.

Requires fonttools for outlining the bundled fonts and Node.js with sharp for
the PNG export. Use --svg-only when only the editable vector asset is needed.
The background, logo and decorative line are retained from social-card.svg.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parents[1]
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def text_group(
    text: str,
    font: TTFont,
    x: float,
    y: float,
    font_size: float,
    max_width: float | None = None,
) -> ET.Element:
    """Outline a line using the same glyph-advance layout as the source card."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    missing = sorted({char for char in text if ord(char) not in cmap})
    if missing:
        raise ValueError(f"Font lacks required characters: {missing}")
    names = [cmap[ord(char)] for char in text]
    advance = sum(glyphs[name].width for name in names)
    scale = font_size / font["head"].unitsPerEm
    if max_width is not None:
        scale = min(scale, max_width / advance)
    group = ET.Element(
        f"{{{SVG_NS}}}g",
        {
            "fill": "#f1efe6",
            "transform": f"translate({x:g} {y:g}) scale({scale:.10f} {-scale:.10f})",
            "aria-label": text,
        },
    )
    cursor = 0
    for name in names:
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        commands = pen.getCommands()
        if commands:
            ET.SubElement(
                group,
                f"{{{SVG_NS}}}path",
                {"transform": f"translate({cursor:g} 0)", "d": commands},
            )
        cursor += glyphs[name].width
    return group


def build_svg() -> Path:
    tree = ET.parse(ROOT / "assets/social-card.svg")
    svg = tree.getroot()
    oswald = instantiateVariableFont(
        TTFont(ROOT / "assets/fonts/oswald-variable.ttf"), {"wght": 600}, inplace=True
    )
    onest = instantiateVariableFont(
        TTFont(ROOT / "assets/fonts/onest-variable.ttf"), {"wght": 400}, inplace=True
    )
    lines = [
        # Retain the established first-line size when removing its final full stop.
        ("translate(63 257)", "ЗНАЙСЦІ ПРАЛОМ", oswald, 63, 257, 66.6324962, 520),
        ("translate(63 351)", "ДА АТАКІ", oswald, 63, 351, 83, None),
        ("translate(65 414)", "Пентэст Web, API і AI-сістэм", onest, 65, 414, 20, None),
    ]
    for transform, text, font, x, y, font_size, max_width in lines:
        matches = [
            (index, child)
            for index, child in enumerate(svg)
            if child.tag == f"{{{SVG_NS}}}g"
            and child.get("transform", "").startswith(transform)
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one source text group at {transform}")
        index, original = matches[0]
        svg.remove(original)
        svg.insert(index, text_group(text, font, x, y, font_size, max_width))
    oswald.close()
    onest.close()

    svg.set("role", "img")
    svg.set("aria-labelledby", "social-card-title social-card-description")
    svg.set("{http://www.w3.org/XML/1998/namespace}lang", "be")
    title = ET.Element(f"{{{SVG_NS}}}title", {"id": "social-card-title"})
    title.text = "DESTINY — пентэст Web, API і AI-сістэм"
    description = ET.Element(f"{{{SVG_NS}}}desc", {"id": "social-card-description"})
    description.text = "Знайсці пралом — Да атакі"
    svg.insert(0, title)
    svg.insert(1, description)

    target = ROOT / "assets/social-card-be.svg"
    tree.write(target, encoding="utf-8", xml_declaration=False)
    with target.open("ab") as output:
        output.write(b"\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--svg-only", action="store_true", help="Skip the PNG export")
    parser.add_argument("--node", default=os.environ.get("CODEX_PRIMARY_RUNTIME_NODE", "node"))
    parser.add_argument("--node-modules", default=os.environ.get("CODEX_PRIMARY_RUNTIME_NODE_MODULES", ""))
    args = parser.parse_args()
    svg = build_svg()
    print(f"Built {svg.relative_to(ROOT)}")
    if args.svg_only:
        return
    png = svg.with_suffix(".png")
    renderer = """
const path = require('node:path');
const [input, output, modules] = process.argv.slice(1);
const sharp = require(modules ? path.join(modules, 'sharp') : 'sharp');
sharp(input).png({ palette: true, colours: 256, effort: 10, compressionLevel: 9 }).toFile(output).catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
"""
    subprocess.run([args.node, "-e", renderer, str(svg), str(png), args.node_modules], check=True)
    print(f"Built {png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
