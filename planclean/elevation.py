"""Cleaning an elevation (exterior or interior) from a builder's PDF.

Keeps the building. Takes off what's written on it and around it: dimensions and their
lines, roof pitch marks, material notes (STONE, SIDING, TRIM, VERIFY ...) and their leaders.
The sheet title (FRONT ELEVATION) and the scale line come back as clean text.
"""
from __future__ import annotations
import difflib, re
from collections import defaultdict
import numpy as np
import pymupdf

from . import ocr
from .vec import polylines, pen_key, in_region, is_filled
from .result import Result, Text

TITLE_VOCAB = ['FRONT', 'REAR', 'LEFT', 'RIGHT', 'SIDE', 'ELEVATION', 'OPTIONAL', 'CAR', 'GARAGE', 'CRAWL', 'SPACE',
               'BASEMENT', 'WALKOUT', 'GARDEN', 'LEVEL', 'PORCH', 'COVERED', 'PATIO', 'DECK', 'EXTENDED', 'TANDEM', 'FULL',
               'PARTIAL', 'FINISHED', 'UNFINISHED', 'INTERIOR', 'KITCHEN', 'BATH', 'MASTER', 'PRIMARY', 'FIREPLACE', 'STAIR']
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
        return b[1] >= Ybot - 2 and (b[3] - b[1]) >= 1.3 * med
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
    # the title and scale under the building become real text in the drawing (the builder's lettering goes)
    progress('Reading the title')
    text_lines, scale_ln = _title_lines(page, pl, M, dregion, Ybot)
    text_boxes = [_grow(b, 1.5) for ln in text_lines for _, b, _ in ln]
    for ln in text_lines:            # the whole line, a little past its ends (a dash the reader skipped: 'ELEVATION -')
        bs = [b for _, b, _ in ln]; h = max(b[3] - b[1] for b in bs)
        text_boxes.append((min(b[0] for b in bs) - 1.5 * h, min(b[1] for b in bs) - 1, max(b[2] for b in bs) + 1.5 * h, max(b[3] for b in bs) + 1))
    drop_w = [(t, _grow(b, 1.5)) for (t, b), k in zip(boxes, keep) if not k]
    numeric = [b for t, b in drop_w if re.fullmatch(r'[0-9:/ ]+|[0-9: ]*PITCH', t)]

    progress('Taking off notes and dimensions')
    gone = set(); masks = []
    rects = [_drect(p, M) for p in pl]
    ends = []
    for i, p in enumerate(pl):
        qs = polylines(p)
        e = [tuple((pymupdf.Point(*q[0]) * M)) for q in qs] + [tuple((pymupdf.Point(*q[-1]) * M)) for q in qs]
        ends.append(e)
    # kept as drawn: the title's letters, the scale line and the title's underline
    below = {i for i, r in enumerate(rects) if any(_inside_frac(r, _grow(b, 1.5)) > 0.6 for _, b in keep_w) or
             (r[1] >= Ybot - 1 and r[0] >= X0 - 1.5 and r[2] <= X1 + 1.5 and (r[2] - r[0]) > 40 and (r[3] - r[1]) < 3)}
    # parts of the building that stick out past its outline (porch posts and their bases) hang off it:
    # upright pieces touching the outline, and what touches those, stay
    def slanted1(p):
        if len(p['items']) != 1 or p['items'][0][0] != 'l': return False
        a, b = p['items'][0][1] * M, p['items'][0][2] * M
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        return dx > 1 and dy > 1
    def touches(r, q, d=1.0): return r[0] <= q[2] + d and q[0] <= r[2] + d and r[1] <= q[3] + d and q[1] <= r[3] + d
    ticks = [rects[i] for i, p in enumerate(pl) if is_filled(p) and max(rects[i][2] - rects[i][0], rects[i][3] - rects[i][1]) < 8]
    def structural(i, p):
        r = rects[i]
        if (p.get('width') or 0) < 0.1 and not is_filled(p): return False        # hairlines: level lines, hatching
        if slanted1(p) or (is_filled(p) and max(r[2] - r[0], r[3] - r[1]) < 8): return False
        if any(_inside_frac(r, b) > 0.6 for _, b in drop_w): return False
        if any(_near(pt, t, 1.5) for pt in ends[i] for t in ticks): return False  # a dimension line ends on a tick
        return True
    outside = lambda r: r[2] <= X0 + 1.5 or r[0] >= X1 - 1.5
    attached = set()
    anchor = [rects[i] for i, p in enumerate(pl) if pen_key(p) == opk or (not outside(rects[i]) and structural(i, p))]
    for _ in range(8):
        new = []
        for i, p in enumerate(pl):
            if i in attached or pen_key(p) == opk: continue
            r = rects[i]
            if not outside(r) or not structural(i, p): continue
            if any(touches(r, q) for q in anchor): new.append(i)
        if not new: break
        attached |= set(new); anchor = anchor + [rects[i] for i in new]
    # what's drawn inside an attached piece (the stone on a post's base) belongs to it
    groups = []                                           # attached pieces that touch, as boxes
    for i in attached:
        r = rects[i]; hit = [g for g in groups if touches(r, g)]
        for g in hit: groups.remove(g)
        for g in hit: r = (min(r[0], g[0]), min(r[1], g[1]), max(r[2], g[2]), max(r[3], g[3]))
        groups.append(r)
    boxes_att = [g for g in groups if (g[2] - g[0]) * (g[3] - g[1]) > 50]
    for i, p in enumerate(pl):
        r = rects[i]
        if i in attached or not outside(r) or any(_inside_frac(r, b) > 0.6 for _, b in drop_w): continue
        if any(r[0] >= q[0] - 0.5 and r[2] <= q[2] + 0.5 and r[1] >= q[1] - 0.5 and r[3] <= q[3] + 0.5 for q in boxes_att): attached.add(i)
    for i, p in enumerate(pl):
        if pen_key(p) == opk: continue
        r = rects[i]; size = max(r[2] - r[0], r[3] - r[1])
        if i in below: continue                                                   # under the building: the title and scale, kept as drawn
        long_bar = is_filled(p) and (r[2] - r[0]) > 0.3 * (X1 - X0)            # the ground line
        # 1. everything to the left or right of the building: dimensions, notes, leaders, level lines
        if not long_bar and i not in attached and (r[2] <= X0 + 1.5 or r[0] >= X1 - 1.5): gone.add(i); continue
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
    def straight(p):
        if len(p['items']) != 1 or p['items'][0][0] != 'l': return None
        a, b = p['items'][0][1] * M, p['items'][0][2] * M
        if abs(a.y - b.y) < 0.5: return 'h'
        if abs(a.x - b.x) < 0.5: return 'v'
        return None
    # 5. dimension lines inside the building's width: a straight line ending on a tick, its ticks,
    #    and the little figures written along it
    dims = []
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk or not straight(p) or (p.get('width') or 0) < 0.1: continue
        r = rects[i]
        if max(r[2] - r[0], r[3] - r[1]) < 12: continue
        if any(_near(pt, t, 1.5) for pt in ends[i] for t in ticks): gone.add(i); dims.append((rects[i], pen_key(p)))
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk or i in attached: continue
        r = rects[i]
        if max(r[2] - r[0], r[3] - r[1]) < 8 and any(pk == pen_key(p) and touches(r, _grow(d, 4), 0) for d, pk in dims): gone.add(i)
    # 6. a line that runs out past the building and stops in empty space (a level or extension line)
    keep_r = [rects[k] for k, q in enumerate(pl) if k not in gone and not (is_filled(q) and max(rects[k][2] - rects[k][0], rects[k][3] - rects[k][1]) < 8)]
    for i, p in enumerate(pl):
        if i in gone or i in below or pen_key(p) == opk or straight(p) != 'h' or i in attached: continue
        r = rects[i]
        if r[0] < X0 - 8 or r[2] > X1 + 8:
            free = [pt for pt in ends[i] if (pt[0] < X0 - 8 or pt[0] > X1 + 8) and
                    not any(touches((pt[0], pt[1], pt[0], pt[1]), q, 1.0) for k, q in enumerate(keep_r) if q != r)]
            if free: gone.add(i)
    tips = [pt for i in gone if not is_filled(pl[i]) for pt in ends[i]]
    for i, p in enumerate(pl):
        if i in gone or i in below or not is_filled(p): continue
        r = rects[i]
        if max(r[2] - r[0], r[3] - r[1]) < 8 and any(_near(pt, r, 2.5) for pt in tips): gone.add(i)

    if text_lines:
        lettering = set()
        for i, r in enumerate(rects):
            if i in gone or (r[2] - r[0]) > 40: continue     # the underline stays (no letter is that wide)
            if any(_inside_frac(r, b) > 0.6 for b in text_boxes): lettering.add(i)
        D = page.derotation_matrix
        def centre(lns):
            bs = [b for ln in lns for _, b, _ in ln]
            c = pymupdf.Point((min(b[0] for b in bs) + max(b[2] for b in bs)) / 2, (min(b[1] for b in bs) + max(b[3] for b in bs)) / 2) * D
            return tx(c.x, c.y)
        def tidy(t):
            t = re.sub(r'\bA(?=TH\b)', '4', t.upper()); t = re.sub(r'\bOP[T1I]?\.?(?=\s|$)', 'OPT.', t)
            t = re.sub(r'EL[A-Z]?[VY]?AT[I1L]ON\b', ' ELEVATION', t)            # also glued on: 'RISHTELEVATION'
            toks = []
            for tok in t.split():
                if tok not in TITLE_VOCAB and len(tok) >= 4 and tok.isalpha():
                    m = difflib.get_close_matches(tok, TITLE_VOCAB, n=1, cutoff=0.75)
                    if m: tok = m[0]
                toks.append(tok)
            return re.sub(r'\s*[-–—]\s*$', ' -', ' '.join(toks)).strip()
        tl = [ln for ln in text_lines if ln not in scale_ln]
        if tl:
            X, Y = centre(tl); res.texts.append(Text('title', [tidy(' '.join(t for t, _, _ in ln)) for ln in tl], X, Y))
        if scale_ln:
            raw = ' '.join(t for t, _, _ in scale_ln[0]).upper()
            m = re.search(r'([0-9IOL]{1,2}) ?/ ?([0-9IOL]{1,2})', raw)
            fx = lambda g: re.sub('[IL]', '1', g).replace('O', '0')
            frac = f'{fx(m.group(1))}/{fx(m.group(2))}' if m else '1/4'
            X, Y = centre(scale_ln[:1]); res.texts.append(Text('note', [f'SCALE: {frac}" = 1\'-0"'], X, Y))
        gone |= lettering

    for i, p in enumerate(pl):
        if i in gone: continue
        role = 'wall' if pen_key(p) == opk else 'misc'
        polys = _fill_outline(p) if is_filled(p) and pen_key(p) != 'fill #ffffff' else None
        if polys:             # a solid shape (the bold ground line): keep it filled
            for poly in polys: res.lines.append((role, [tx(*v) for v in poly], True))
            continue
        for q in polylines(p):
            res.lines.append((role, [tx(*v) for v in q], is_filled(p) and pen_key(p) != 'fill #ffffff' and len(q) > 2))
    res.original = [[tx(*v) for v in q] for i in gone for q in polylines(pl[i])]

    res.stats = dict(removed=len(gone), words=len(words), kind='elevation')
    return res


def _title_lines(page, pl, M, dregion, Ybot):
    """The title's lines and the scale line under the building, each [(text, display box, word)], read from
    a picture of just that band (big lettering reads best on its own, at a small size)."""
    band = pymupdf.Rect(dregion[0], max(Ybot - 120, dregion[1]), dregion[2], dregion[3]) * page.derotation_matrix   # some of the house too: a thin strip reads badly
    band.normalize(); b = (band.x0, band.y0, band.x1, band.y1)
    ps = [p for p in pl if in_region(p, b)]
    if not ps: return [], []
    best = None
    for sc in (1.5, 2.0, 2.5):
        try: ws = ocr.merge_same_line(ocr._read_area(ps, b, page.rotation, sc))
        except Exception: continue
        ws = [w for w in ws if not w['vert']]
        def box(w):
            r = pymupdf.Rect(w['bb']) * M; r.normalize(); return (r.x0, r.y0, r.x1, r.y1)
        items = [(w['n'].upper().strip(), box(w), w) for w in ws]
        heads = [it for it in items if re.search(r'ELEV|EVATION|LEVAT|EL[EA]?[VY]?AT[I1L]ON', it[0]) and it[1][1] >= Ybot - 4]
        if not heads: continue
        head = max(heads, key=lambda it: it[2]['score'])
        h = head[1][3] - head[1][1]
        def same_line(it, ref):
            c = ref[1]; r = it[1]
            return (min(r[3], c[3]) - max(r[1], c[1])) > 0.5 * min(r[3] - r[1], c[3] - c[1]) and 0.6 < (r[3] - r[1]) / max(c[3] - c[1], 1e-6) < 1.6
        lines = [sorted([it for it in items if same_line(it, head) and min(abs(it[1][0] - head[1][2]), abs(head[1][0] - it[1][2]), 0 if it is head else 1e9) < 4 * h] or [head], key=lambda z: z[1][0])]
        # a second title line of the same size just under it ('OPT. 4TH CAR GARAGE')
        for it in sorted(items, key=lambda z: z[1][1]):
            if any(it in ln for ln in lines) or re.search(r'SCALE|SCALF|5CALE', it[0]): continue
            gy = it[1][1] - lines[-1][0][1][3]
            if 0 <= gy < 1.0 * h and 0.7 < (it[1][3] - it[1][1]) / max(h, 1e-6) < 1.4 and it[1][0] < head[1][2] + 2 * h and it[1][2] > head[1][0] - 2 * h:
                lines.append(sorted([v for v in items if same_line(v, it)], key=lambda z: z[1][0]))
            if len(lines) == 3: break
        scale = [it for it in items if re.search(r'SCALE|SCALF|5CALE', it[0]) and it[1][1] >= head[1][1]]
        scale_lines = [sorted([v for v in items if same_line(v, scale[0])], key=lambda z: z[1][0])] if scale else []
        score = sum(it[2]['score'] for ln in lines for it in ln) + (0.5 if scale else 0)
        if best is None or score > best[0]: best = (score, lines + scale_lines, scale_lines)
    return (best[1], best[2]) if best else ([], [])


def _fill_outline(p):
    """A filled path drawn as straight pieces, as closed outlines (a solid is often drawn as two triangles). None if it isn't one."""
    items = p.get('items') or []
    if len(items) < 2 or any(it[0] != 'l' for it in items): return None
    polys, cur = [], None
    for it in items:
        a, b = (it[1].x, it[1].y), (it[2].x, it[2].y)
        if cur and abs(a[0] - cur[-1][0]) < 0.01 and abs(a[1] - cur[-1][1]) < 0.01: cur.append(b)
        else:
            if cur: polys.append(cur)
            cur = [a, b]
    polys.append(cur)
    polys = [q for q in polys if len(q) > 3]
    return polys or None


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
