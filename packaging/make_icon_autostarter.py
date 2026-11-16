"""N1MM Autostarter icon: a white A on the POUNCer blue tile, an orange
spot inside the A, two waves going out to the upper right."""
# python packaging/make_icon_autostarter.py pouncer/icon/autostarter   -> autostarter.ico, autostarter.png, autostarter-preview.png
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
    ax, ay = n * 0.5, n * 0.19                       # apex
    lx, rx, fy = n * 0.21, n * 0.79, n * 0.80        # feet
    def leg(x0, y0, x1, y1):
        d.line([(x0, y0), (x1, y1)], fill=W, width=int(w))
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=W)
    leg(ax, ay, lx, fy)
    leg(ax, ay, rx, fy)
    # crossbar, between the legs
    cy = n * 0.635
    t = (cy - ay) / (fy - ay)
    d.rectangle([ax + (lx - ax) * t, cy - w / 2 * 0.9, ax + (rx - ax) * t, cy + w / 2 * 0.9], fill=W)
    # the spot in the A
    sr = n * (0.055 if not simple else 0.065)
    sy = n * (0.495 if not simple else 0.50)
    d.ellipse([ax - sr, sy - sr, ax + sr, sy + sr], fill=(255, 152, 0, 255))
    if not simple:
        for i, rad in enumerate((0.33, 0.41)):
            Rw = n * rad
            d.arc([ax - Rw, sy - Rw, ax + Rw, sy + Rw], start=-52, end=-14,
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
