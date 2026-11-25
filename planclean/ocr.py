"""Read builder text that was plotted as line strokes (no real text in the PDF).

Text is read in the sheet's display orientation (PDF pages are often stored sideways),
so normal text reads left-to-right. Each word keeps its box in display coordinates
(dbb, used for joining words and lines) and in page coordinates (bb)."""
from __future__ import annotations
import math, re
import numpy as np, cv2
from .vec import raster

_engine = None
def engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine

def norm(t):
    t = re.sub(r"[^A-Z0-9/&.()' -]", '', t.upper().replace('$', '&')).strip()
    # an O among digits is a zero ('22OV 4OA' -> '220V 40A')
    return re.sub(r"\b(?=[0-9O]*[0-9])[0-9O]+[A-Z]?\b", lambda m: m.group().replace('O', '0'), t)

_ROT = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

def _unrotate_pts(b, rot, shape_before):
    """Map pixel coords in an image rotated by `rot` back to the image before rotation."""
    H, W = shape_before
    if rot is None: return b
    if rot == cv2.ROTATE_90_COUNTERCLOCKWISE: return np.c_[W - 1 - b[:, 1], b[:, 0]]
    if rot == cv2.ROTATE_90_CLOCKWISE: return np.c_[b[:, 1], H - 1 - b[:, 0]]
    if rot == cv2.ROTATE_180: return np.c_[W - 1 - b[:, 0], H - 1 - b[:, 1]]

TILE, PAD = 512, 128      # points: page-fixed reading tiles, and how far past its edges each tile reads

def _disp(x, y, rot):
    """Page point -> display orientation (up to a shift, which nothing here depends on)."""
    return {90: (-y, x), 180: (-x, -y), 270: (y, -x)}.get(rot % 360, (x, y))

def read_words(paths, region, page_rotation=0, scale=4):
    """Read the text in region. The text finder is sensitive to the image's edges and size, so the
    same word could read differently (or not at all) depending on how the plan box was drawn. So the
    sheet is read in fixed tiles measured from the page origin: away from the box's edges a word's
    reading doesn't depend on the box."""
    return merge_same_line(_read_tiles(paths, region, page_rotation, scale, 0))

def _read_tiles(paths, region, page_rotation, scale, shift):
    words = []
    for tx in range(int(math.floor((region[0] - shift) / TILE) * TILE + shift), int(math.ceil(region[2])), TILE):
        for ty in range(int(math.floor((region[1] - shift) / TILE) * TILE + shift), int(math.ceil(region[3])), TILE):
            rect = (tx - PAD, ty - PAD, tx + TILE + PAD, ty + TILE + PAD)
            tp = [p for p in paths if p['rect'].x1 >= rect[0] and p['rect'].x0 <= rect[2]
                  and p['rect'].y1 >= rect[1] and p['rect'].y0 <= rect[3]]
            if not tp: continue
            for w in _read_area(tp, rect, page_rotation, scale):
                if tx <= w['cx'] < tx + TILE and ty <= w['cy'] < ty + TILE \
                        and region[0] <= w['cx'] <= region[2] and region[1] <= w['cy'] <= region[3]:
                    words.append(w)
    return words

def _overlap(a, b):
    """Intersection over the smaller box."""
    iw = min(a[2], b[2]) - max(a[0], b[0]); ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0: return 0.0
    return iw * ih / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))

def _read_area(paths, region, page_rotation, scale):
    W, H = round((region[2] - region[0]) * scale), round((region[3] - region[1]) * scale)
    native = 255 - raster(paths, region, scale)[:H, :W]
    r0 = _ROT.get(page_rotation % 360)
    disp = cv2.rotate(native, r0) if r0 is not None else native
    words = []
    for extra in (None, cv2.ROTATE_90_CLOCKWISE):   # horizontal text, then vertical (reads bottom-to-top)
        im = cv2.rotate(disp, extra) if extra is not None else disp
        res, _ = engine()(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR), use_cls=False)   # we turn the image ourselves; the flip guesser only adds upside-down misreads
        for box, t, sc in (res or []):
            d = _unrotate_pts(np.array(box, float), extra, disp.shape)      # display pixels
            ddb = (d[:, 0].min(), d[:, 1].min(), d[:, 0].max(), d[:, 1].max())
            vert = (ddb[3] - ddb[1]) > (ddb[2] - ddb[0])
            if (extra is None) == vert: continue
            n = _unrotate_pts(d, r0, native.shape)                           # native pixels
            xs = n[:, 0] / scale + region[0]; ys = n[:, 1] / scale + region[1]
            bb = (xs.min(), ys.min(), xs.max(), ys.max())
            q = np.array([_disp(x, y, page_rotation) for x, y in ((bb[0], bb[1]), (bb[2], bb[3]))])
            dbb = (q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max())   # display points, page-fixed
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            dup = [w for w in words if abs(cx - w['cx']) < 3 and abs(cy - w['cy']) < 3]
            if dup:
                if float(sc) > dup[0]['score']: words.remove(dup[0])
                else: continue
            words.append(dict(text=t.strip(), n=norm(t), bb=bb, dbb=dbb, cx=cx, cy=cy, score=float(sc),
                              size=min(dbb[2] - dbb[0], dbb[3] - dbb[1]), vert=vert))
    return words

def typed_words(lines, page_rotation=0):
    """Typed PDF text as words, the same shape the text reader gives (exact, so no reading needed)."""
    out = []
    for l in lines:
        if not norm(l['text']): continue
        bb = l['bbox']
        q = np.array([_disp(x, y, page_rotation) for x, y in ((bb[0], bb[1]), (bb[2], bb[3]))])
        dbb = (q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max())
        ddx, ddy = _disp(*l['dir'], page_rotation)
        out.append(dict(text=l['text'], n=norm(l['text']), bb=tuple(bb), dbb=dbb, cx=(bb[0] + bb[2]) / 2, cy=(bb[1] + bb[3]) / 2,
                        score=1.0, size=min(dbb[2] - dbb[0], dbb[3] - dbb[1]), vert=abs(ddy) > abs(ddx), typed=True,
                        fsize=l.get('size', 0)))
    return out

def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

def _absorb(a, b):
    a['bb'] = _union(a['bb'], b['bb']); a['dbb'] = _union(a['dbb'], b['dbb'])
    a['cx'] = (a['bb'][0] + a['bb'][2]) / 2; a['cy'] = (a['bb'][1] + a['bb'][3]) / 2
    a['score'] = min(a['score'], b['score'])

def _axes(w):
    """(along-reading interval, across interval) in display coords; vertical text reads upward."""
    x0, y0, x1, y1 = w['dbb']
    return ((-y1, -y0), (x0, x1)) if w['vert'] else ((x0, x1), (y0, y1))

def merge_same_line(words, gap_max=6, size_tol=3.5):
    changed = True
    while changed:
        changed = False
        for a in words:
            for b in words:
                if a is b or a['vert'] != b['vert']: continue
                (aa, ap), (ba, bp) = _axes(a), _axes(b)
                ov = min(ap[1], bp[1]) - max(ap[0], bp[0]); perp = min(ap[1] - ap[0], bp[1] - bp[0])
                if aa[0] > ba[0]: continue          # a must come first along the line
                gap = ba[0] - aa[1]
                if ov > 0.6 * perp and -8 < gap < gap_max and abs(a['size'] - b['size']) < size_tol:
                    a['text'] = a['text'] + ' ' + b['text']; a['n'] = norm(a['text']); _absorb(a, b)
                    words.remove(b); changed = True; break
            if changed: break
    return words

def stack_lines(words):
    """Join stacked note lines (e.g. 'TO LIGHT' over 'ABV') into one multi-line note."""
    for w in words: w.setdefault('lines', [w['n']])
    merged = True
    while merged:
        merged = False
        for a in words:
            for b in words:
                if a is b or a['vert'] != b['vert']: continue
                (aa, ap), (ba, bp) = _axes(a), _axes(b)
                if ap[0] > bp[0]: continue          # a must be the upper line
                ov = min(aa[1], ba[1]) - max(aa[0], ba[0]); gap = bp[0] - ap[1]
                if ov > 5 and gap < 6:
                    a['lines'] = a['lines'] + b['lines']; _absorb(a, b)
                    words.remove(b); merged = True; break
            if merged: break
    return words
