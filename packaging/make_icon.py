"""POUNCer icon: a bold C on a rounded blue tile, an orange spot in its mouth.

    python packaging/make_icon.py pouncer/icon/pouncer   -> .ico, .png, -preview.png
"""
import sys
from PIL import Image, ImageDraw

S = 1024

def tile(size, simple=False):
    k = 4                                   # supersample
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # vertical gradient
    grad = Image.new("RGBA", (n, n))
    top, bot = (16, 42, 74), (21, 101, 192)
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / (n - 1)
        gd.line([(0, y), (n, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    mask = Image.new("L", (n, n), 0)
    pad = int(n * 0.03)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, n - pad, n - pad], radius=int(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # the C: a thick ring, open to the right
    cx, cy = n * 0.47, n * 0.5
    r = n * (0.33 if not simple else 0.35)
    w = int(n * (0.13 if not simple else 0.17))
    d.arc([cx - r, cy - r, cx + r, cy + r], start=48, end=312, fill=(255, 255, 255, 255), width=w)
    # round ends
    import math
    for a in (48, 312):
        rr = r - w / 2
        x, y = cx + rr * math.cos(math.radians(a)), cy + rr * math.sin(math.radians(a))
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=(255, 255, 255, 255))
    # the spot in its mouth
    sr = n * (0.085 if not simple else 0.11)
    sx = cx + r * (0.62 if not simple else 0.55)
    d.ellipse([sx - sr, cy - sr, sx + sr, cy + sr], fill=(255, 152, 0, 255))
    if not simple:
        # two small waves from the spot
        for i, rad in enumerate((0.17, 0.25)):
            R = n * rad
            d.arc([sx - R, cy - R, sx + R, cy + R], start=-38, end=38,
                  fill=(255, 183, 77, 255 - i * 70), width=int(n * 0.03))
    return img.resize((size, size), Image.LANCZOS)

out = sys.argv[1]
big = tile(256)
sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
frames = [tile(s, simple=s <= 32) for s in sizes]
frames[-1].save(out + ".ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
tile(512).save(out + ".png")
# preview: all sizes on dark and light
pv = Image.new("RGBA", (900, 330), (32, 32, 32, 255))
x = 10
for f in frames:
    pv.paste(f, (x, 10), f); x += f.width + 10
x = 10
lt = Image.new("RGBA", (900, 160), (243, 243, 243, 255)); pv.paste(lt, (0, 280 - 10))
for s in (16, 24, 32, 48):
    f = tile(s, simple=s <= 32); pv.paste(f, (x, 285), f); x += s + 14
pv.save(out + "-preview.png")
