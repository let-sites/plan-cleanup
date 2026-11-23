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
    return re.sub(r"[^A-Z0-9/&.()' -]", '', t.upper().replace('$', '&')).strip()

_ROT = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

def _unrotate_pts(b, rot, shape_before):
    """Map pixel coords in an image rotated by `rot` back to the image before rotation."""
    H, W = shape_before
    if rot is None: return b
    if rot == cv2.ROTATE_90_COUNTERCLOCKWISE: return np.c_[W - 1 - b[:, 1], b[:, 0]]
    if rot == cv2.ROTATE_90_CLOCKWISE: return np.c_[b[:, 1], H - 1 - b[:, 0]]
    if rot == cv2.ROTATE_180: return np.c_[W - 1 - b[:, 0], H - 1 - b[:, 1]]

SNAP = 128      # points (512 px at scale 4, a whole number of the text finder's 32 px steps)

def _disp(x, y, rot):
    """Page point -> display orientation (up to a shift, which nothing here depends on)."""
    return {90: (-y, x), 180: (-x, -y), 270: (y, -x)}.get(rot % 360, (x, y))

def read_words(paths, region, page_rotation=0, scale=4):
    """The text finder is sensitive to the image's edges and size, so the same word can read
    differently (or not at all) depending on how the plan box was drawn. Read two page-fixed areas,
    the box snapped outward to a 128 pt page grid and that area grown by 64 pt, and keep the better
    reading of each word: boxes drawn a little differently read the same images, and a word one
    reading misses the other usually finds."""
    snap = (math.floor(region[0] / SNAP) * SNAP, math.floor(region[1] / SNAP) * SNAP,
            math.ceil(region[2] / SNAP) * SNAP, math.ceil(region[3] / SNAP) * SNAP)
    words = _read_area(paths, snap, page_rotation, scale)
    grown = (snap[0] - 64, snap[1] - 64, snap[2] + 64, snap[3] + 64)
    for w in _read_area(paths, grown, page_rotation, scale):
        same = [v for v in words if _overlap(v['bb'], w['bb']) > 0.4]
        if not same: words.append(w)
        elif w['score'] > max(v['score'] for v in same) + 0.05:
            for v in same: words.remove(v)
            words.append(w)
    words = [w for w in words if region[0] <= w['cx'] <= region[2] and region[1] <= w['cy'] <= region[3]]
    return merge_same_line(words)

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
        res, _ = engine()(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR))
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
