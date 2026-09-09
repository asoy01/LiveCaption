"""Draw the LiveCaption application icon and write LiveCaption.ico.

The icon is used by the Start menu shortcut that InstallToStartMenu.bat makes.
Put the result at etc/LiveCaption.ico; the installer picks it up if it is there.

This needs Pillow, which LiveCaption itself does not use. Run it in any
environment that has Pillow, for example a scratch pixi environment:

    pixi run python scripts/make_icon.py --design B --out etc/LiveCaption.ico

Every shape is drawn at four times the target size and then reduced, so the
edges stay smooth at 16 and 32 pixels. Those two sizes are what the Start menu
and the taskbar actually show, so check them before you accept a design.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:
    sys.exit("Pillow is required. Run this script in an environment that has it.")

# The web UI palette (GitHub dark), so the icon matches the control page.
BLUE = (47, 129, 247, 255)       # #2f81f7
DARK = (13, 17, 23, 255)         # #0d1117
WHITE = (255, 255, 255, 255)
GREEN = (63, 185, 80, 255)       # #3fb950

SS = 4                            # supersampling factor
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def _font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    names = {"bold": "segoeuib.ttf", "black": "seguibl.ttf"}
    candidates = [Path("C:/Windows/Fonts") / names[weight], Path("C:/Windows/Fonts/arialbd.ttf")]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise SystemExit("No bold font found.")


def _tile(draw: ImageDraw.ImageDraw, n: int, fill=BLUE) -> None:
    """The rounded square that every design sits on."""
    draw.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=fill)


def design_a(n: int) -> Image.Image:
    """A screen with two caption lines in it."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _tile(d, n)

    # The screen is a solid white block, not an outline. A one-pixel outline
    # turns grey at 16 px and the icon then reads as a plain window.
    left, right = n * 0.135, n * 0.865
    top, bottom = n * 0.235, n * 0.765
    d.rounded_rectangle([left, top, right, bottom], radius=int(n * 0.10), fill=WHITE)

    # Two caption lines cut out of the screen, the second one shorter.
    bar = n * 0.085
    bl = left + n * 0.10
    br = right - n * 0.10
    y1 = bottom - n * 0.285
    y2 = y1 + bar + n * 0.075
    d.rounded_rectangle([bl, y1, br, y1 + bar], radius=bar / 2, fill=BLUE)
    d.rounded_rectangle([bl, y2, bl + (br - bl) * 0.55, y2 + bar], radius=bar / 2, fill=BLUE)
    return img


def design_b(n: int) -> Image.Image:
    """A speech bubble with caption lines in it."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _tile(d, n)

    left, right = n * 0.14, n * 0.86
    top, bottom = n * 0.20, n * 0.66
    d.rounded_rectangle([left, top, right, bottom], radius=int(n * 0.13), fill=WHITE)
    # The tail, on the lower left.
    d.polygon([(n * 0.30, bottom - n * 0.02),
               (n * 0.30, n * 0.86),
               (n * 0.52, bottom - n * 0.02)], fill=WHITE)

    bar = n * 0.075
    bl = left + n * 0.11
    br = right - n * 0.11
    y1 = top + n * 0.115
    y2 = y1 + bar + n * 0.055
    d.rounded_rectangle([bl, y1, br, y1 + bar], radius=bar / 2, fill=BLUE)
    d.rounded_rectangle([bl, y2, bl + (br - bl) * 0.58, y2 + bar], radius=bar / 2, fill=BLUE)
    return img


def design_c(n: int) -> Image.Image:
    """EN over two caption lines, with a live dot."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _tile(d, n, fill=DARK)

    # A blue inner edge, so the near-black tile has an outline on dark taskbars.
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22),
                        outline=BLUE, width=max(1, int(n * 0.05)))

    font = _font(int(n * 0.44), "black")
    d.text((n * 0.5, n * 0.42), "EN", font=font, fill=WHITE, anchor="mm")

    bar = n * 0.06
    bl, br = n * 0.24, n * 0.76
    y = n * 0.70
    d.rounded_rectangle([bl, y, br, y + bar], radius=bar / 2, fill=BLUE)
    d.rounded_rectangle([bl, y + bar * 2, bl + (br - bl) * 0.55, y + bar * 3],
                        radius=bar / 2, fill=BLUE)

    r = n * 0.055
    d.ellipse([n * 0.78 - r, n * 0.20 - r, n * 0.78 + r, n * 0.20 + r], fill=GREEN)
    return img


DESIGNS = {"A": design_a, "B": design_b, "C": design_c}


def render(design: str, size: int) -> Image.Image:
    """Draw one design at one size, supersampled."""
    big = DESIGNS[design](size * SS)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(design: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = [render(design, s) for s in SIZES]
    # Pillow reduces the master itself, so hand it the largest and let it do the
    # rest; the shapes are simple enough that the reduction holds up.
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}  (design {design}, sizes {', '.join(str(s) for s in SIZES)})")


def contact_sheet(out: Path) -> None:
    """One PNG showing every design at the sizes Windows actually draws."""
    shown = [16, 24, 32, 48, 64]
    pad, gap, label_w = 16, 18, 34
    row_h = 112
    width = label_w + pad + sum(s + gap for s in shown) + 256 + gap + pad
    height = row_h * len(DESIGNS) * 2 + pad

    sheet = Image.new("RGB", (width, height), (245, 246, 248))
    d = ImageDraw.Draw(sheet)
    small = _font(13)
    y = pad // 2

    for name in DESIGNS:
        for bg in ((245, 246, 248), (13, 17, 23)):
            d.rectangle([0, y, width, y + row_h], fill=bg)
            ink = (30, 30, 30) if bg[0] > 128 else (235, 235, 235)
            d.text((pad // 2, y + row_h // 2), name, font=small, fill=ink, anchor="lm")
            x = label_w
            for s in shown:
                icon = render(name, s)
                sheet.paste(icon, (x, y + (row_h - s) // 2), icon)
                d.text((x + s // 2, y + row_h - 10), str(s), font=small, fill=ink, anchor="mm")
                x += s + gap
            big = render(name, 64).resize((96, 96), Image.Resampling.NEAREST)
            sheet.paste(big, (x + 8, y + (row_h - 96) // 2), big)
            y += row_h

    sheet.save(out)
    print(f"wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--design", choices=sorted(DESIGNS), help="which design to write as .ico")
    p.add_argument("--out", type=Path, default=Path("etc/LiveCaption.ico"))
    p.add_argument("--sheet", type=Path, help="write a comparison sheet of all designs here")
    args = p.parse_args()

    if args.sheet:
        contact_sheet(args.sheet)
    if args.design:
        write_ico(args.design, args.out)
    if not args.sheet and not args.design:
        p.error("give --design, --sheet, or both")


if __name__ == "__main__":
    main()
