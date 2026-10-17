"""Draws build/scribe.ico — the amber lamp on a dark tile, like the app's favicon.

Generated at build time (needs Pillow) so the repo stays text-only.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "scribe.ico"
SIZE = 256

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=56, fill=(38, 38, 36, 255))
r = 44
glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
for i in range(18, 0, -1):  # soft halo, like the lamp's glow in the app
    gd.ellipse((SIZE / 2 - r - i * 2, SIZE / 2 - r - i * 2, SIZE / 2 + r + i * 2, SIZE / 2 + r + i * 2),
               fill=(232, 147, 58, 6))
img = Image.alpha_composite(img, glow)
ImageDraw.Draw(img).ellipse((SIZE / 2 - r, SIZE / 2 - r, SIZE / 2 + r, SIZE / 2 + r), fill=(232, 147, 58, 255))
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {OUT}")
