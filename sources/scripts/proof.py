"""Render a proof sheet of a variable font across its Flare and Weight axes.

Usage: python scripts/proof.py FONT.ttf OUT.png [--text TEXT] [--size PX] [--weights 100,400,700] [--flares 0,50,100]
"""

import argparse

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

DEFAULT_TEXT = "HAMBURGEFONSTIV hamburgefonstiv 0123456789"
MARGIN = 30


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("font")
    parser.add_argument("out")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--size", type=int, default=64)
    parser.add_argument("--weights", default="100,400,700")
    parser.add_argument("--flares", default="0,50,100")
    args = parser.parse_args()

    axes = TTFont(args.font)["fvar"].axes
    weights = [float(v) for v in args.weights.split(",")]
    flares = [float(v) for v in args.flares.split(",")]
    font = ImageFont.truetype(args.font, args.size)
    label_font = ImageFont.load_default()
    line = int(args.size * 1.45)

    rows = [(weight, flare) for weight in weights for flare in flares]
    width = int(font.getlength(args.text) * 1.15) + 2 * MARGIN
    image = Image.new("L", (width, line * len(rows) + 2 * MARGIN), 255)
    draw = ImageDraw.Draw(image)
    for row, (weight, flare) in enumerate(rows):
        location = {"wght": weight, "FLAR": flare}
        font.set_variation_by_axes([location.get(axis.axisTag, axis.defaultValue) for axis in axes])
        y = MARGIN + row * line
        draw.text((MARGIN, y), f"wght {weight:g}  FLAR {flare:g}", font=label_font, fill=110)
        draw.text((MARGIN, y + 12), args.text, font=font, fill=0)
    image.save(args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
