"""Convert a supplied image to a multi-resolution Windows ICO."""

import argparse
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with Image.open(args.source) as source:
        image = source.convert("RGBA")
        if image.width != image.height:
            edge = max(image.size)
            square = Image.new("RGBA", (edge, edge))
            square.paste(image, ((edge - image.width) // 2, (edge - image.height) // 2))
            image = square
        image.save(args.destination, format="ICO",
                   sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    with Image.open(args.destination) as icon:
        print(f"Created {args.destination}: {sorted(icon.ico.sizes())}")


if __name__ == "__main__":
    main()
