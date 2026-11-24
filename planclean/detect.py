"""Find builder symbols on a sheet by comparing against examples the user clicked once."""
from __future__ import annotations
import numpy as np, cv2
from .vec import raster, polylines, draw_text

S = 6  # template resolution, px per point (matches Profile.TEMPLATE_SCALE)

def symbol_image(paths, region, texts=()):
    img = raster(paths, region, S)
    if texts: draw_text(img, region, S, texts)          # typed letters ('S' on a switch) count as part of the drawing
    img = img.astype(np.float32)
    return cv2.GaussianBlur(img, (0, 0), 1.2)

def _closed(p):
    it = p['items']
    a, b = it[0][1], it[-1][-1]
    return abs(a.x - b.x) + abs(a.y - b.y) < 0.2

def anchors(paths):
    """Candidate symbol centres: small circles, and compact glyph-like stroke groups."""
    out = []
    for p in paths:
        r = p['rect']
        ncurve = sum(i[0] == 'c' for i in p['items'])
        # a circle: four or more curves, or (in some CAD exports) a many-sided polygon
        nline = sum(i[0] == 'l' for i in p['items'])
        roundish = ncurve >= 4 or (nline >= 12 and _closed(p))
        if roundish and 3 < r.width < 20 and abs(r.width - r.height) < 0.4:
            out.append(('circle', (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
        elif len(p['items']) >= 4 and max(r.width, r.height) < 8 and min(r.width, r.height) > 2.5:
            out.append(('glyph', (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return out

def nearest_anchor(anc, x, y, maxd=8):
    best = None
    for k, ax, ay in anc:
        d = np.hypot(ax - x, ay - y)
        if d < maxd and (best is None or d < best[0]): best = (d, k, ax, ay)
    return best

def cut_template(img, region, cx, cy, half, half_y=None):
    hy = half_y or half
    x0 = int(round((cx - half - region[0]) * S)); y0 = int(round((cy - hy - region[1]) * S))
    n = int(round(2 * half * S)); ny = int(round(2 * hy * S))
    out = np.zeros((ny, n), dtype=img.dtype)         # stays the full size and centred, even at the edge of the box
    sx0, sy0 = max(0, x0), max(0, y0); sx1, sy1 = min(img.shape[1], x0 + n), min(img.shape[0], y0 + ny)
    if sx1 > sx0 and sy1 > sy0: out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = img[sy0:sy1, sx0:sx1]
    return out

def classify(img, region, examples, anc, search=2.0, wall_bias=('wall_light', 'ceiling_light', 0.06)):
    """Return detections: dicts with cls, score, x, y (page pts), per-class scores."""
    T = []
    for ex in examples:
        t = ex.image()
        for k in ex.rotations:
            T.append((ex, k, np.ascontiguousarray(np.rot90(t, k))))
    found = []
    def match_at(ax, ay, kind):
        best = None; per = {}
        for ex, k, t in T:
            if ex.kind != kind: continue
            m = max(ex.half, getattr(ex, 'half_y', 0) or ex.half) + search      # turned, a tall box is a wide one
            x0 = int(round((ax - m - region[0]) * S)); y0 = int(round((ay - m - region[1]) * S)); n = int(round(2 * m * S))
            if x0 < 0 or y0 < 0: continue
            win = img[y0:y0 + n, x0:x0 + n]
            if win.shape[0] < t.shape[0] or win.shape[1] < t.shape[1]: continue
            res = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            per[ex.cls] = max(per.get(ex.cls, 0), mx)
            if mx >= ex.threshold and (best is None or mx > best['score']):
                best = dict(cls=ex.cls, score=float(mx), x=region[0] + (x0 + loc[0] + t.shape[1] / 2) / S,
                            y=region[1] + (y0 + loc[1] + t.shape[0] / 2) / S)
        if best:
            a, b, margin = wall_bias
            if best['cls'] == a and per.get(b, 0) >= best['score'] - margin:
                best['cls'] = b
            best['scores'] = {k: round(float(v), 3) for k, v in per.items() if v > 0.4}
        return best
    for kind, ax, ay in anc:
        h = match_at(ax, ay, kind)
        if h: found.append(h)
    # examples marked 'scan' have no anchor shape: search the whole sheet
    for ex, k, t in T:
        if ex.kind != 'scan': continue
        res = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= max(ex.threshold, 0.75))
        for yy, xx in zip(ys, xs):
            found.append(dict(cls=ex.cls, score=float(res[yy, xx]), x=region[0] + (xx + t.shape[1] / 2) / S,
                              y=region[1] + (yy + t.shape[0] / 2) / S, scores={}))
    found.sort(key=lambda h: -h['score'])
    keep = []
    for h in found:
        if all((h['x'] - k['x']) ** 2 + (h['y'] - k['y']) ** 2 > 16 for k in keep): keep.append(h)
    return keep
