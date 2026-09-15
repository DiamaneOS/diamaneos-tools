#!/usr/bin/env python3
"""Build portable Lift SVG assets from the selected geometry and pinned fonts.

Requires Python 3 and fonttools==4.60.1. No network access is used.
"""
from pathlib import Path
import hashlib
import html
import json

from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

ROOT = Path(__file__).resolve().parent
SOURCE = json.loads((ROOT / "source.json").read_text())
OUT = ROOT / "assets"
OUT.mkdir(exist_ok=True)


def svg(width, height, content, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">\n'
            f'  <title id="title">{html.escape(title)}</title>\n{content}\n</svg>\n')


def planes(colors, transform=None):
    parts = '\n'.join(f'<path fill="{color}" d="{path}"/>' for color, path in zip(colors, SOURCE["planes"]))
    return f'<g transform="{transform}">{parts}</g>' if transform else parts


def type_outlines():
    """Use source font advances with explicit brand tracking; outline every glyph."""
    x = 0
    paths = []
    for text, filename in [("Diamane", "InstrumentSans-Medium.ttf"), ("OS", "InstrumentSans-Regular.ttf")]:
        font = TTFont(ROOT / "fonts" / filename)
        upem = font["head"].unitsPerEm
        glyphs = font.getGlyphSet()
        cmap = font.getBestCmap()
        for character in text:
            name = cmap[ord(character)]
            pen = SVGPathPen(glyphs)
            glyphs[name].draw(pen)
            scale = 52 / upem
            paths.append(f'<path transform="translate({x:.4f} 0) scale({scale:.8f} {-scale:.8f})" d="{pen.getCommands()}"/>')
            x += font["hmtx"][name][0] * scale + SOURCE["wordmark"]["trackingEm"] * 52
        font.close()
    return '\n'.join(paths), x - SOURCE["wordmark"]["trackingEm"] * 52


type_paths, type_width = type_outlines()
lockup_width = round(110 + type_width + 16)


def lockup(accent, ink, transform=None):
    content = planes([accent] * 3, "translate(10 15) scale(.72)")
    content += f'\n<g fill="{ink}" transform="translate(110 70)">{type_paths}</g>'
    return f'<g transform="{transform}">{content}</g>' if transform else content


def write(name, content):
    (OUT / name).write_text(content)


for theme, palette in SOURCE["palette"].items():
    bg, ink, accent = (palette[key] for key in ["background", "ink", "accent"])
    shades = [palette["planeSoft"], accent, palette["planeMid"]]
    write(f'lift-mark-{theme}.svg', svg(108, 106, planes([accent] * 3), "DiamaneOS Lift mark"))
    write(f'diamaneos-lockup-{theme}.svg', svg(lockup_width, 106, lockup(accent, ink), "DiamaneOS"))
    banner = f'<rect width="1200" height="400" fill="{bg}"/>'
    banner += planes(shades, "translate(790 -88) rotate(-11 54 53) scale(5.15)")
    banner += lockup(accent, ink, "translate(64 141) scale(1.05)")
    write(f'lift-banner-{theme}.svg', svg(1200, 400, banner, "DiamaneOS Lift banner"))
    portrait = f'<rect width="1440" height="3200" fill="{bg}"/>'
    portrait += planes(shades, "translate(100 1710) rotate(10 700 650) scale(17)")
    write(f'lift-wallpaper-portrait-{theme}.svg', svg(1440, 3200, portrait, "Lift portrait wallpaper"))
    desktop = f'<rect width="3840" height="2160" fill="{bg}"/>'
    desktop += planes(shades, "translate(1890 500) rotate(-8 800 800) scale(26)")
    write(f'lift-wallpaper-desktop-{theme}.svg', svg(3840, 2160, desktop, "Lift landscape wallpaper"))
    avatar = f'<rect width="1024" height="1024" fill="{bg}"/>'
    avatar += planes([accent] * 3, "translate(188 194) scale(6)")
    write(f'lift-avatar-{theme}.svg', svg(1024, 1024, avatar, "DiamaneOS Lift avatar"))

for name, color in [("black", "#000000"), ("white", "#ffffff")]:
    write(f'lift-mark-{name}.svg', svg(108, 106, planes([color] * 3), "DiamaneOS Lift mark"))
    write(f'diamaneos-lockup-{name}.svg', svg(lockup_width, 106, lockup(color, color), "DiamaneOS"))

write('lift-data.js', 'window.diamaneLift = ' + json.dumps(SOURCE, separators=(',', ':')) + ';\n')
manifest = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(OUT.glob('*.svg'))}
write('svg-sha256.json', json.dumps(manifest, indent=2) + '\n')
print(f'Generated {len(manifest)} SVG assets from Lift source.json.')
