"""App icon: a big outlined push broom with sparkles on a blue tile. Usage: python tools/make_icon.py out.png"""
import sys, math
from PIL import Image, ImageDraw, ImageFilter
SS = 4; N = 1024 * SS
def P(v): return int(v * SS)
def make(variant):
    img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    bg = Image.new('RGBA', (N, N)); top, bot = (92, 166, 212), (36, 92, 140)
    bd = ImageDraw.Draw(bg)
    for y in range(0, N, 2):
        t = y / N; bd.rectangle([0, y, N, y + 2], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    m = Image.new('L', (N, N), 0); ImageDraw.Draw(m).rounded_rectangle([P(64), P(64), P(960), P(960)], radius=P(200), fill=255)
    img.paste(bg, (0, 0), m); d = ImageDraw.Draw(img)
    W = (255, 255, 255, 255)
    if variant == 'plan':
        WT = P(54); C = (255, 255, 255, 235)
        def wall(x0, y0, x1, y1): d.rectangle([P(min(x0,x1))-WT//2, P(min(y0,y1))-WT//2, P(max(x0,x1))+WT//2, P(max(y0,y1))+WT//2], fill=C)
        L, T, R, B = 170, 170, 854, 854
        wall(L, T, R, T); wall(R, T, R, B); wall(L, T, L, B); wall(L, B, 300, B); wall(470, B, R, B)
        wall(500, T, 500, 380)
        d.line([P(470), P(B), P(470), P(B-170)], fill=C, width=P(18))
        d.arc([P(470-170), P(B-170), P(470+170), P(B+170)], 180, 270, fill=C, width=P(12))
    # broom, big, diagonal
    ang = math.radians(-35); cx, cy = 500, 450; S = 1.4
    OL = (30, 34, 42, 255); OW = P(16)
    def rot(x, y):
        x *= S; y *= S
        return (cx + x*math.cos(ang) - y*math.sin(ang), cy + x*math.sin(ang) + y*math.cos(ang))
    def pts(loc): return [(P(a), P(b)) for a, b in (rot(x, y) for x, y in loc)]
    def rrect(x0, y0, x1, y1, r, n=10):
        out = []
        for qx, qy, a0 in ((x1-r, y0+r, -90), (x1-r, y1-r, 0), (x0+r, y1-r, 90), (x0+r, y0+r, 180)):
            for k in range(n+1):
                t = math.radians(a0 + 90*k/n); out.append((qx + r*math.cos(t), qy + r*math.sin(t)))
        return out
    def shape(loc, fill):
        p = pts(loc); d.polygon(p, fill=fill); d.line(p + [p[0]], fill=OL, width=OW, joint='curve')
    handle = rrect(-30, -300, 30, 10, 30); ferr = rrect(-56, -10, 56, 28, 14)
    head = rrect(-140, 20, 140, 82, 28); straw = [(-126, 74), (126, 74), (172, 250), (-172, 250)]
    sh = Image.new('RGBA', (N, N), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
    for loc in (handle, ferr, head, straw):
        sd.polygon([(a + P(16), b + P(22)) for a, b in pts(loc)], fill=(0, 0, 0, 100))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(P(16)))); d = ImageDraw.Draw(img)
    shape(straw, (250, 208, 90, 255))
    for k in range(1, 7):
        x0 = -110 + 220*k/7; x1 = -160 + 320*k/7
        p0, p1 = rot(x0, 100), rot(x1, 190)
        d.line([P(p0[0]), P(p0[1]), P(p1[0]), P(p1[1])], fill=(214, 160, 50, 255), width=P(12))
    for k in range(1, 7):
        x = -172 + 344*k/7; h = (46, 34, 52, 38, 48, 36)[k-1]
        p0, p1 = rot(x, 250), rot(x*0.97, 250 - h)
        d.line([P(p0[0]), P(p0[1]), P(p1[0]), P(p1[1])], fill=OL, width=P(14))
    shape(handle, (156, 100, 56, 255)); d.polygon(pts(rrect(-18, -285, -6, -20, 6)), fill=(198, 142, 90, 255))
    shape(ferr, (176, 180, 188, 255))
    shape(head, (156, 100, 56, 255)); d.polygon(pts(rrect(-120, 32, 120, 46, 7)), fill=(198, 142, 90, 255))
    def star(x, y, r):
        p = []
        for k in range(8):
            t = math.radians(45*k - 90); rr = r if k % 2 == 0 else r*0.3
            p.append((P(x + rr*math.cos(t)), P(y + rr*math.sin(t))))
        d.polygon(p, fill=W)
    star(235, 690, 60); star(330, 800, 34); star(200, 540, 28)
    return img.resize((1024, 1024), Image.LANCZOS)
make('solo').save(sys.argv[1])
