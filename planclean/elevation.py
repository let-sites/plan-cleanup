"""Cleaning an elevation (exterior or interior) from a builder's PDF.

Keeps the building. Takes off what's written on it and around it: dimensions and their
lines, roof pitch marks, material notes (STONE, SIDING, TRIM, VERIFY ...) and their leaders.
The sheet title (FRONT ELEVATION) and the scale line come back as clean text.
"""
from __future__ import annotations
import re
from collections import defaultdict
import numpy as np
import pymupdf

from . import ocr
from .vec import polylines, pen_key, in_region, is_filled
from .result import Result, Text

KEEP_WORDS = re.compile(r'ELEVATION|^SCALE')


def _drect(p, M):
    r = p['rect'] * M
    return (min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1), max(r.y0, r.y1))


def _grow(b, d):
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def _inside_frac(r, b):
    iw = min(r[2], b[2]) - max(r[0], b[0]); ih = min(r[3], b[3]) - max(r[1], b[1])
    if iw < 0 or ih < 0: return 0.0
    a = max(1e-6, (r[2] - r[0])) * max(1e-6, (r[3] - r[1]))
    return max(iw, 1e-6) * max(ih, 1e-6) / a


def _near(pt, b, d):
    return b[0] - d <= pt[0] <= b[2] + d and b[1] - d <= pt[1] <= b[3] + d


def outline_pen(paths, M, width):
    """The pen the building's outline is drawn with: the thickest stroke that runs across the box."""
    by = defaultdict(list)
    for p in paths:
        if not is_filled(p): by[pen_key(p)].append(p)
    best = None
    for k, ps in by.items():
        rs = [_drect(p, M) for p in ps]
        span = max(r[2] for r in rs) - min(r[0] for r in rs)
        w = ps[0].get('width') or 0
        if len(ps) >= 5 and span > 0.3 * width and (best is None or w > best[0]): best = (w, k)
    return best[1] if best else None


def clean_elevation(page, region, inches_per_pt, progress=lambda m: None) -> Result:
    K = inches_per_pt; x0, y0 = region[0], region[1]
    def tx(x, y): return ((x - x0) * K, -(y - y0) * K)
    res = Result(); res.to_page = lambda X, Y: (X / K + x0, -Y / K + y0)
    M = page.rotation_matrix
    progress('Reading the sheet')
    pl = [p for p in page.get_drawings() if in_region(p, region)]
    if not pl: return res
    dregion = _drect({'rect': pymupdf.Rect(region)}, M)
    opk = outline_pen(pl, M, dregion[2] - dregion[0])
    walls = [p for p in pl if pen_key(p) == opk]
    wr = [_drect(p, M) for p in walls]
    X0, X1 = (min(r[0] for r in wr), max(r[2] for r in wr)) if wr else (dregion[0], dregion[2])
    Ybot = max(r[3] for r in wr) if wr else dregion[3]

    progress('Reading the notes')
    words = ocr.read_words([p for p in pl if pen_key(p) != opk], region, page.rotation)
    def dbox(w):
        b = w['bb']; r = (pymupdf.Rect(b) * M)
        return (min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1), max(r.y0, r.y1))
    # the sheet title (and its scale line) stays; every other word is a note to take off.
    # Title words: ELEVATION / SCALE, the big lettering under the building, and anything on their line.
    boxes = [(w['n'].upper().strip(), dbox(w)) for w in words]
    hs = sorted(b[3] - b[1] for _, b in boxes) or [0]
    med = hs[len(hs) // 2]
    def titleish(t, b):
        if KEEP_WORDS.search(t) or re.search(r'ELEV|EVATION', t): return True
        return b[1] >= Ybot - 2 and (b[3] - b[1]) >= 1.6 * med
    keep = [titleish(t, b) for t, b in boxes]
    for _ in range(2):                                   # same line, same size as a title word
        for i, (t, b) in enumerate(boxes):
            if keep[i]: continue
            for j, (_, c) in enumerate(boxes):
                if not keep[j]: continue
                ov = min(b[3], c[3]) - max(b[1], c[1]); h = b[3] - b[1]; hc = c[3] - c[1]
                if ov > 0.5 * min(h, hc) and 0.6 < h / max(hc, 1e-6) < 1.6 and min(abs(b[0] - c[2]), abs(c[0] - b[2])) < 4 * hc:
                    keep[i] = True; break
    keep_w = [(t, _grow(b, 1.5)) for (t, b), k in zip(boxes, keep) if k]
    drop_w = [(t, _grow(b, 1.5)) for (t, b), k in zip(boxes, keep) if not k]
    numeric = [b for t, b in drop_w if re.fullmatch(r'[0-9:/ ]+|[0-9: ]*PITCH', t)]

    progress('Taking off notes and dimensions')
    gone = set(); masks = []
    rects = [_drect(p, M) for p in pl]
    # kept as drawn: the title's letters, the scale line and the title's underline
    below = {i for i, r in enumerate(rects) if r[1] >= Ybot - 1 and r[0] >= X0 - 1.5 and r[2] <= X1 + 1.5 and
             (any(_inside_frac(r, _grow(b, 1.5)) > 0.6 for _, b in keep_w) or ((r[2] - r[0]) > 40 and (r[3] - r[1]) < 3))}
    ends = []
    for i, p in enumerate(pl):
        qs = polylines(p)
        e = [tuple((pymupdf.Point(*q[0]) * M)) for q in qs] + [tuple((pymupdf.Point(*q[-1]) * M)) for q in qs]
        ends.append(e)
    for i, p in enumerate(pl):
        if pen_key(p) == opk: continue
        r = rects[i]; size = max(r[2] - r[0], r[3] - r[1])
        if i in below: continue                                                   # under the building: the title and scale, kept as drawn
        long_bar = is_filled(p) and (r[2] - r[0]) > 0.3 * (X1 - X0)            # the ground line
        # 1. everything to the left or right of the building: dimensions, notes, leaders, level lines
        if not long_bar and (r[2] <= X0 + 1.5 or r[0] >= X1 - 1.5): gone.add(i); continue
        # 2. the letters of a note
        if any(_inside_frac(r, b) > 0.6 for _, b in drop_w): gone.add(i); continue
        # 3. pitch marks: small shapes next to the 5 / 12 / PITCH numbers
        if size < 22 and any(_inside_frac(r, _grow(b, 14)) > 0.5 for b in numeric): gone.add(i); continue
        # 4. a small white patch is a label's background (the 12 and 5 of a pitch mark)
        if is_filled(p) and pen_key(p) == 'fill #ffffff' and size < 15: gone.add(i); masks.append(_grow(r, 0.5)); continue
    # the label's figures sit on its patch; the little triangle beside it goes too
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk: continue
        r = rects[i]; size = max(r[2] - r[0], r[3] - r[1])
        if any(_inside_frac(r, m) > 0.6 for m in masks): gone.add(i); continue
        if size < 32 and any(_inside_frac(r, _grow(m, 18)) > 0.9 for m in masks): gone.add(i)
    # 4. leaders: strokes that start at a removed note and slant (or end in an arrowhead) toward the
    #    building, plus the arrowheads at their tips. A straight siding course or post that merely
    #    passes a note isn't one.
    heads = [rects[i] for i, p in enumerate(pl) if is_filled(p) and pen_key(p) != 'fill #ffffff' and max(rects[i][2] - rects[i][0], rects[i][3] - rects[i][1]) < 8]
    def slanted(p):
        for it in p['items']:
            a, b = it[1] * M, it[2] * M
            dx, dy = abs(b.x - a.x), abs(b.y - a.y)
            if dx > 1 and dy > 1 and 0.09 < dy / dx < 11: return True
        return False
    leader = set()
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk or is_filled(p): continue
        if len(p['items']) > 4 or any(it[0] != 'l' for it in p['items']): continue
        e = ends[i]
        at_note = [pt for pt in e if any(_near(pt, b, 4) for _, b in drop_w)]
        if not at_note: continue
        far = [pt for pt in e if pt not in at_note]
        if slanted(p) or any(_near(pt, h, 2.5) for pt in far for h in heads): gone.add(i); leader.add(i)
    # the short level stub between a note and its slanted leader
    tipsL = [pt for i in leader for pt in ends[i]]
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk or is_filled(p) or len(p['items']) != 1 or p['items'][0][0] != 'l': continue
        r = rects[i]
        if max(r[2] - r[0], r[3] - r[1]) < 30 and any(_near(pt, b, 4) for pt in ends[i] for _, b in drop_w) and \
           any(abs(pt[0] - q[0]) < 1.5 and abs(pt[1] - q[1]) < 1.5 for pt in ends[i] for q in tipsL): gone.add(i)
    tips = [pt for i in gone if not is_filled(pl[i]) for pt in ends[i]]
    for i, p in enumerate(pl):
        if i in gone or i in below or not is_filled(p): continue
        r = rects[i]
        if max(r[2] - r[0], r[3] - r[1]) < 8 and any(_near(pt, r, 2.5) for pt in tips): gone.add(i)

    for i, p in enumerate(pl):
        if i in gone: continue
        role = 'wall' if pen_key(p) == opk else 'misc'
        for q in polylines(p):
            res.lines.append((role, [tx(*v) for v in q], is_filled(p) and len(q) > 2))
    res.original = [[tx(*v) for v in q] for i in gone for q in polylines(pl[i])]

    res.stats = dict(removed=len(gone), words=len(words), kind='elevation')
    return res


def _untx(v, K, x0, y0):
    return (v[0] / K + x0, -v[1] / K + y0)


def looks_like_elevation(page, region):
    """A quick read of the box's lower part: an elevation's title says ELEVATION (and plans don't)."""
    pl = [p for p in page.get_drawings() if in_region(p, region)]
    if not pl: return False
    M = page.rotation_matrix
    d = _drect({'rect': pymupdf.Rect(region)}, M)
    # the bottom third of the box as you see it, back in page points
    band = pymupdf.Rect(d[0], d[1] + (d[3] - d[1]) * 0.66, d[2], d[3]) * page.derotation_matrix
    band.normalize(); b = (band.x0, band.y0, band.x1, band.y1)
    ps = [p for p in pl if in_region(p, b)]
    if not ps: return False
    for sc in (1.5, 2.5):
        try: words = ocr._read_area(ps, b, page.rotation, sc)
        except Exception: return False
        txt = ' '.join(w['n'].upper() for w in words)
        if re.search(r'ELEVAT|ELEV\b', txt): return True
    return False
