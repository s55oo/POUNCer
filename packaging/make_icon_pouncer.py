"""A P icon in the same style as the POUNCer C: white P on a blue tile,
an orange spot in its bowl, two waves going out to the right."""
# python packaging/make_icon_pouncer.py pouncer/icon/pouncer   -> pouncer.ico, pouncer.png, pouncer-preview.png
import math, sys
from PIL import Image, ImageDraw

def tile(size, simple=False, open_bowl=False):
    k = 4; n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    grad = Image.new("RGBA", (n, n)); gd = ImageDraw.Draw(grad)
    top, bot = (16, 42, 74), (21, 101, 192)
    for y in range(n):
        t = y / (n - 1)
        gd.line([(0, y), (n, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    mask = Image.new("L", (n, n), 0)
    pad = int(n * 0.03)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, n - pad, n - pad], radius=int(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    W = (255, 255, 255, 255)
    w = n * (0.12 if not simple else 0.15)
    sx = n * 0.31                                     # stem centre line
    bcx, bcy = n * 0.47, n * 0.40                     # bowl centre
    r = n * (0.19 if not simple else 0.18)            # bowl centre-line radius
    top, y1 = bcy - r - w / 2, n * 0.80
    # stem, rounded at both ends
    d.rounded_rectangle([sx - w / 2, top, sx + w / 2, y1 + w / 2], radius=w / 2, fill=W)
    # top and bottom of the bowl, from the stem to the arc
    d.rectangle([sx, bcy - r - w / 2, bcx, bcy - r + w / 2], fill=W)
    d.rectangle([sx, bcy + r - w / 2, bcx, bcy + r + w / 2], fill=W)
    # the bowl arc on the right
    R = r + w / 2
    d.arc([bcx - R, bcy - R, bcx + R, bcy + R], start=-90, end=90, fill=W, width=int(w))
    # the spot in the bowl
    sr = n * (0.065 if not simple else 0.07)
    d.ellipse([bcx - sr, bcy - sr, bcx + sr, bcy + sr], fill=(255, 152, 0, 255))
    if not simple:
        # waves out of the bowl, lower right
        cx, cy = bcx, bcy
        for i, rad in enumerate((0.33, 0.41)):
            Rw = n * rad
            d.arc([cx - Rw, cy - Rw, cx + Rw, cy + Rw], start=8, end=58,
                  fill=(255, 183, 77, 255 - i * 70), width=int(n * 0.03))
    return img.resize((size, size), Image.LANCZOS)

out = sys.argv[1]
sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
frames = [tile(s, simple=s <= 32) for s in sizes]
frames[-1].save(out + ".ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
tile(512).save(out + ".png")
pv = Image.new("RGBA", (900, 330), (32, 32, 32, 255))
x = 10
for f in frames:
    pv.paste(f, (x, 10), f); x += f.width + 10
pv.paste(Image.new("RGBA", (900, 70), (243, 243, 243, 255)), (0, 270))
x = 10
for s in (16, 24, 32, 48):
    f = tile(s, simple=s <= 32); pv.paste(f, (x, 280), f); x += s + 14
pv.save(out + "-preview.png")
