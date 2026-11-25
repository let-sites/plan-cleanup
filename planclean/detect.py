"""Find builder symbols on a sheet by comparing against examples the user clicked once."""
from __future__ import annotations
import numpy as np, cv2
from .vec import raster, polylines, draw_text

S = 6  # template resolution, px per point (matches Profile.TEMPLATE_SCALE)
SCAN_F = 3   # the whole-sheet search runs at 1/SCAN_F size first

def symbol_image(paths, region, texts=()):
    img = raster(paths, region, S)
    if texts: draw_text(img, region, S, texts)          # typed letters ('S' on a switch) count as part of the drawing
    img = img.astype(np.float32)
    return cv2.GaussianBlur(img, (0, 0), 1.2)

def symbol_paths(paths, role):
    """What a symbol can be drawn with: the electrical (and unsorted) pens, plus small solid dots whatever their
    pen (a pendant's black dot is often a fill the pens list calls a wall)."""
    from .vec import is_filled, pen_key
    out = []
    for p in paths:
        if role(p) in ('electrical', 'misc', 'opening'): out.append(p); continue
        r = p['rect']
        if is_filled(p) and pen_key(p) != 'fill #ffffff' and 1.5 < r.width < 10 and abs(r.width - r.height) < 0.25 * r.width:
            out.append(p)
    return out

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
        elif roundish and p.get('fill') is not None and 1.5 < r.width <= 3 and abs(r.width - r.height) < 0.4:
            out.append(('circle', (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))     # a small solid dot (a pendant)
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

def similar(a, b):
    """How alike two symbol pictures are (-1..1), whatever their sizes."""
    if a.shape != b.shape: a = cv2.resize(a, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_AREA)
    a = a.astype(np.float32).ravel(); b = b.astype(np.float32).ravel()
    a = a - a.mean(); b = b - b.mean(); d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / d if d > 0 else 0.0

def distinct(examples, same=0.92):
    """The examples less near-copies of one another (the review learns many alike: matching each again is
    slow and finds nothing new). Taught ones are kept over learned ones."""
    keep = []
    for ex in sorted(examples, key=lambda e: bool(getattr(e, 'learned', False))):
        try: im = ex.image()
        except Exception: keep.append((ex, None)); continue
        dup = False
        for k, kim in keep:
            if kim is not None and k.cls == ex.cls and k.kind == ex.kind and set(ex.rotations) <= set(k.rotations) and \
                    abs(k.threshold - ex.threshold) < 0.05 and similar(im, kim) >= same:
                dup = True; break
        if not dup: keep.append((ex, im))
    return [e for e, _ in keep]

def classify(img, region, examples, anc, search=2.0, wall_bias=('wall_light', 'ceiling_light', 0.06)):
    """Return detections: dicts with cls, score, x, y (page pts), per-class scores."""
    T = []
    for ex in distinct(examples):
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
    # examples marked 'scan' have no anchor shape: search the whole sheet. First at a third of the size (fast,
    # light), then each likely spot again at full size. Only the best hit per spot: a loose example used to
    # give millions of hits, gigabytes of memory and minutes of sorting.
    F = SCAN_F
    small = None
    for ex, k, t in T:
        if ex.kind != 'scan': continue
        if float(t.std()) < 1.0: continue                    # nothing drawn in it: it would match everywhere
        if small is None:
            small = cv2.resize(img, (img.shape[1] // F, img.shape[0] // F), interpolation=cv2.INTER_AREA)
            I1, I2 = cv2.integral2(small)                    # window sums, to tell blank paper from drawing
        ts = cv2.resize(t, (max(3, t.shape[1] // F), max(3, t.shape[0] // F)), interpolation=cv2.INTER_AREA)
        if ts.shape[0] > small.shape[0] or ts.shape[1] > small.shape[1] or float(ts.std()) < 0.5: continue
        thr = max(ex.threshold, 0.75)
        res = cv2.matchTemplate(small, ts, cv2.TM_CCOEFF_NORMED)
        # over blank paper the score is noise (it divides by almost nothing, and can read 1.0): only where
        # something is drawn
        h_, w_ = ts.shape
        win_var = (I2[h_:h_ + res.shape[0], w_:w_ + res.shape[1]] - I2[:res.shape[0], w_:w_ + res.shape[1]]
                   - I2[h_:h_ + res.shape[0], :res.shape[1]] + I2[:res.shape[0], :res.shape[1]]) / (h_ * w_)
        win_mean = (I1[h_:h_ + res.shape[0], w_:w_ + res.shape[1]] - I1[:res.shape[0], w_:w_ + res.shape[1]]
                    - I1[h_:h_ + res.shape[0], :res.shape[1]] + I1[:res.shape[0], :res.shape[1]]) / (h_ * w_)
        res[(win_var - win_mean ** 2) < 4.0] = 0; res[~np.isfinite(res)] = 0; del win_var, win_mean
        r = max(2, int(min(ts.shape) * 0.5))
        peak = (res >= thr - 0.12) & (res >= cv2.dilate(res, np.ones((2 * r + 1, 2 * r + 1), np.uint8)))
        ys, xs = np.nonzero(peak); del peak
        if len(ys) > 400:                                    # far too many to be real symbols: keep the best
            o = np.argsort(-res[ys, xs])[:400]; ys, xs = ys[o], xs[o]
        del res
        pad = 2 * F
        for yy, xx in zip(ys, xs):
            X0, Y0 = max(0, xx * F - pad), max(0, yy * F - pad)
            win = img[Y0:Y0 + t.shape[0] + 2 * pad, X0:X0 + t.shape[1] + 2 * pad]
            if win.shape[0] < t.shape[0] or win.shape[1] < t.shape[1]: continue
            rr = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED); _, mx, _, loc = cv2.minMaxLoc(rr)
            if mx < thr: continue
            found.append(dict(cls=ex.cls, score=float(mx), x=region[0] + (X0 + loc[0] + t.shape[1] / 2) / S,
                              y=region[1] + (Y0 + loc[1] + t.shape[0] / 2) / S, scores={}))
    found.sort(key=lambda h: -h['score'])
    keep = []
    for h in found:
        if all((h['x'] - k['x']) ** 2 + (h['y'] - k['y']) ** 2 > 16 for k in keep): keep.append(h)
    return keep
