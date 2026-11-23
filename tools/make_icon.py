import sys, math
from PIL import Image, ImageDraw, ImageFilter
SS = 4; N = 1024 * SS
def P(v): return int(v * SS)
img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
# background: rounded square, vertical gradient
bg = Image.new('RGBA', (N, N))
top, bot = (88, 160, 204), (43, 104, 150)
for y in range(N):
    t = y / N; c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
    ImageDraw.Draw(bg).line([(0, y), (N, y)], fill=c + (255,))
mask = Image.new('L', (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle([P(64), P(64), P(960), P(960)], radius=P(190), fill=255)
img.paste(bg, (0, 0), mask)
d = ImageDraw.Draw(img)
W = (255, 255, 255, 255); WT = P(30)   # wall thickness
def wall(x0, y0, x1, y1): d.rectangle([P(min(x0,x1)) - WT//2, P(min(y0,y1)) - WT//2, P(max(x0,x1)) + WT//2, P(max(y0,y1)) + WT//2], fill=W)
# floor plan: outer walls with an entry door gap on the bottom wall and a window on the top
L, T, R, B = 200, 230, 824, 800
wall(L, T, 610, T); wall(740, T, R, T)                 # top wall, window gap 610-740
for yy in (P(T) - WT//2 + P(3), P(T), P(T) + WT//2 - P(3)):
    d.line([P(610), yy, P(740), yy], fill=W, width=P(6))   # window: three thin lines across the opening
wall(R, T, R, B)                                        # right wall
wall(L, B, 330, B); wall(450, B, R, B)                  # bottom wall, front door gap 330-450
wall(L, T, L, B)                                        # left wall
# front door: leaf + swing arc
d.line([P(450), P(B), P(450), P(B - 120)], fill=W, width=P(10))
d.arc([P(450 - 120), P(B - 120), P(450 + 120), P(B + 120)], start=180, end=270, fill=W, width=P(6))
# interior walls with doorway gaps
wall(520, T, 520, 420); wall(520, 520, 520, 560)        # vertical partition with door gap 420-520
wall(L, 560, 300, 560); wall(400, 560, 520, 560)        # horizontal partition, door gap 300-400
# small door swings at interior openings
d.line([P(520), P(420), P(620), P(420)], fill=W, width=P(9))   # door leaf, open
d.arc([P(520 - 100), P(420 - 100), P(520 + 100), P(420 + 100)], start=0, end=90, fill=W, width=P(5))
# broom: flat, outlined push-broom style; brown handle, grey ferrule, block head, flared straw with notched ends
ang = math.radians(-24)
cx, cy = 640, 610
OL = (40, 44, 52, 255); OW = P(10)
def rot(x, y): return (cx + x * math.cos(ang) - y * math.sin(ang), cy + x * math.sin(ang) + y * math.cos(ang))
def pts(local): return [(P(a), P(b)) for a, b in (rot(x, y) for x, y in local)]
def rrect(x0, y0, x1, y1, r, n=8):
    out = []
    for (qx, qy, a0) in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(n + 1):
            t = math.radians(a0 + 90 * k / n); out.append((qx + r * math.cos(t), qy + r * math.sin(t)))
    return out
def shape(local, fill):
    p = pts(local); d.polygon(p, fill=fill); d.line(p + [p[0]], fill=OL, width=OW, joint='curve')
# shadow
sh = Image.new('RGBA', (N, N), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
for loc in (rrect(-24, -470, 24, 0, 24), rrect(-120, 10, 120, 70, 26), [(-112, 70), (112, 70), (150, 250), (-150, 250)]):
    sd.polygon([(a + P(14), b + P(18)) for a, b in pts(loc)], fill=(0, 0, 0, 85))
img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(P(12))))
d = ImageDraw.Draw(img)
# straw: flares out, bottom edge cut with notches
straw = [(-112, 64), (112, 64), (150, 250), (-150, 250)]
shape(straw, (246, 206, 98, 255))
# slits cut up from the flat bottom edge, slightly uneven
for k in range(1, 9):
    x = -150 + 300 * k / 9; hgt = (44, 30, 50, 36, 46, 32, 48, 38)[k - 1]
    p0, p1 = rot(x, 250), rot(x * 0.97, 250 - hgt)
    d.line([P(p0[0]), P(p0[1]), P(p1[0]), P(p1[1])], fill=OL, width=P(9))
    d.ellipse([P(p1[0]) - P(4.5), P(p1[1]) - P(4.5), P(p1[0]) + P(4.5), P(p1[1]) + P(4.5)], fill=OL)
# a few bristle lines
for k in range(1, 8):
    x0 = -100 + 200 * k / 8; x1 = -140 + 280 * k / 8
    p0, p1 = rot(x0, 90), rot(x1 * 0.98, 160)
    d.line([P(p0[0]), P(p0[1]), P(p1[0]), P(p1[1])], fill=(222, 172, 64, 255), width=P(6))
# handle (brown) and ferrule (grey)
shape(rrect(-24, -470, 24, 10, 24), (150, 98, 58, 255))
hl = pts(rrect(-14, -455, -4, -20, 5)); d.polygon(hl, fill=(190, 136, 88, 255))
shape(rrect(-46, -10, 46, 26, 12), (168, 172, 178, 255))
# head block
shape(rrect(-124, 20, 124, 74, 24), (150, 98, 58, 255))
hb = pts(rrect(-108, 30, 108, 40, 5)); d.polygon(hb, fill=(190, 136, 88, 255))
# sparkles
def star(x, y, r):
    p = []
    for k in range(8):
        t = math.radians(45 * k - 90); rr = r if k % 2 == 0 else r * 0.28
        p.append((P(x + rr * math.cos(t)), P(y + rr * math.sin(t))))
    d.polygon(p, fill=(255, 255, 255, 255))
star(300, 660, 34); star(255, 735, 22); star(350, 610, 18)
out = img.resize((1024, 1024), Image.LANCZOS)
out.save(sys.argv[1])
