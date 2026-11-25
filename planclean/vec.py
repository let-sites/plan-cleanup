"""Vector paths from PDF pages: geometry helpers, pens and rasterising."""
from __future__ import annotations
import numpy as np, cv2

def _bez(p0, p1, p2, p3, n=10):
    t = np.linspace(0, 1, n)[:, None]
    P = [np.array([p.x, p.y]) for p in (p0, p1, p2, p3)]
    return (1-t)**3*P[0] + 3*(1-t)**2*t*P[1] + 3*(1-t)*t**2*P[2] + t**3*P[3]

def polylines(path):
    """List of Nx2 arrays (page points) for one PyMuPDF drawing path."""
    out = []
    for it in path['items']:
        k = it[0]
        if k == 'l':
            out.append(np.array([[it[1].x, it[1].y], [it[2].x, it[2].y]]))
        elif k == 'c':
            out.append(_bez(it[1], it[2], it[3], it[4]))
        elif k == 'qu':
            q = it[1]
            out.append(np.array([[q.ul.x, q.ul.y], [q.ur.x, q.ur.y], [q.lr.x, q.lr.y], [q.ll.x, q.ll.y], [q.ul.x, q.ul.y]]))
        elif k == 're':
            r = it[1]
            out.append(np.array([[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1], [r.x0, r.y0]]))
    return out

def pen_key(path) -> str:
    """Identify the 'pen' a path was drawn with (line weight + colour + fill)."""
    w = round(path.get('width') or 0, 2)
    c = path.get('color') or path.get('fill') or (0, 0, 0)
    c = tuple(int(round(v * 255)) for v in c[:3])
    filled = path['type'] in ('f', 'fs')
    if filled and w == 0:
        return f"fill #{c[0]:02x}{c[1]:02x}{c[2]:02x}"
    return f"{w:.2f} #{c[0]:02x}{c[1]:02x}{c[2]:02x}"

def layer_key(path) -> str:
    return path.get('layer') or ''

def drop_layers(paths, profile):
    """Leave out what's drawn on the builder PDF layers set to Leave off (optional items, say)."""
    off = {k for k, v in (getattr(profile, 'pdf_layer_roles', None) or {}).items() if v == 'remove'}
    return [p for p in paths if layer_key(p) not in off] if off else paths

def is_filled(path):
    return path['type'] in ('f', 'fs')

def raster(paths, region, scale, thick=1):
    """Draw paths (white on black) into a uint8 image covering region at scale px/pt."""
    x0, y0, x1, y1 = region
    W = int((x1 - x0) * scale) + 1
    H = int((y1 - y0) * scale) + 1
    img = np.zeros((H, W), np.uint8)
    for p in paths:
        fill = is_filled(p)
        if fill:      # a solid shape: its pieces (a dot's four curves) make one outline, filled
            pcs = polylines(p)
            if pcs and len(pcs) > 1 and max(p['rect'].width, p['rect'].height) < 10:
                q = ((np.concatenate(pcs) - [x0, y0]) * scale * 16).round().astype(np.int32)
                cv2.fillPoly(img, [q], 255, cv2.LINE_AA, shift=4)
        for pl in polylines(p):
            q = ((pl - [x0, y0]) * scale * 16).round().astype(np.int32)
            if fill and len(q) > 2:
                cv2.fillPoly(img, [q], 255, cv2.LINE_AA, shift=4)
            cv2.polylines(img, [q], False, 255, thick, cv2.LINE_AA, shift=4)
    return img

# ---------- typed text (some builders' PDFs have real text, not letters drawn as lines)
def page_text(page, region=None, exclude=()):
    """Typed text lines on a PDF page, in page points (the same space as the drawings):
    dicts with text, bbox, dir (reading direction), size and chars [(c, bbox)]."""
    out = []
    try: blocks = page.get_text('rawdict')['blocks']
    except Exception: return out
    for b in blocks:
        for l in b.get('lines', []):
            chars = [(c['c'], tuple(c['bbox'])) for s in l['spans'] for c in s['chars']]
            txt = ''.join(c for c, _ in chars).strip()
            if not txt: continue
            x0, y0, x1, y1 = l['bbox']; cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if region is not None and not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]): continue
            if any(e[0] <= cx <= e[2] and e[1] <= cy <= e[3] for e in exclude): continue
            out.append(dict(text=txt, bbox=(x0, y0, x1, y1), dir=tuple(l['dir']), chars=chars,
                            size=max((s['size'] for s in l['spans']), default=0)))
    return out

_GLYPHS = {}
def _glyph(c):
    """A letter drawn upright, cropped to its ink (the same drawing every time, so taught and found symbols compare)."""
    g = _GLYPHS.get(c)
    if g is None:
        f = cv2.FONT_HERSHEY_SIMPLEX
        (tw, th), base = cv2.getTextSize(c, f, 1.0, 2)
        cv = np.zeros((th + base + 10, tw + 10), np.uint8); cv2.putText(cv, c, (5, th + 5), f, 1.0, 255, 2, cv2.LINE_AA)
        ys, xs = np.nonzero(cv)
        g = cv[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(xs) else None
        _GLYPHS[c] = g
    return g

def draw_text(img, region, scale, lines):
    """Draw typed text into a raster made by raster(): each letter fills its own box, turned with its line."""
    x0r, y0r = region[0], region[1]; H, W = img.shape
    for l in lines:
        dx, dy = l['dir']; k = int(round(np.degrees(np.arctan2(dy, dx)) / 90.0)) % 4
        for c, (a, b, cc, d) in l['chars']:
            if c.isspace(): continue
            g = _glyph(c)
            if g is None: continue
            bw, bh = (cc - a) * scale, (d - b) * scale
            gw, gh = (bw, bh) if k % 2 == 0 else (bh, bw)          # the letter's own width (along the line) and height
            iw, ih = max(1, int(round(gw * 0.8))), max(1, int(round(gh * 0.62)))
            t = np.rot90(cv2.resize(g, (iw, ih), interpolation=cv2.INTER_AREA), -k)
            px = int(round(((a + cc) / 2 - x0r) * scale - t.shape[1] / 2)); py = int(round(((b + d) / 2 - y0r) * scale - t.shape[0] / 2))
            sx0, sy0 = max(0, px), max(0, py); sx1, sy1 = min(W, px + t.shape[1]), min(H, py + t.shape[0])
            if sx1 <= sx0 or sy1 <= sy0: continue
            img[sy0:sy1, sx0:sx1] = np.maximum(img[sy0:sy1, sx0:sx1], t[sy0 - py:sy1 - py, sx0 - px:sx1 - px])
    return img

def text_anchors(lines, most=3):
    """Short typed labels ('S', 'S3', '$', 'WP') are symbols, or parts of them: places to look for one."""
    out = []
    for l in lines:
        if len(l['text'].replace(' ', '')) <= most and (' ' not in l['text'].strip() or not l.get('chars')):
            out.append(('glyph', (l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2)); continue
        # a line can hold several ('S3 S' for a pair of switches side by side): each short word on its own
        word = []
        for c, bb in list(l.get('chars') or []) + [(' ', None)]:
            if c.strip(): word.append(bb); continue
            if word and len(word) <= most:
                x0 = min(b[0] for b in word); y0 = min(b[1] for b in word); x1 = max(b[2] for b in word); y1 = max(b[3] for b in word)
                out.append(('glyph', (x0 + x1) / 2, (y0 + y1) / 2))
            word = []
    return out

def in_region(path, region):
    r = path['rect']; x0, y0, x1, y1 = region
    return r.x0 >= x0 and r.x1 <= x1 and r.y0 >= y0 and r.y1 <= y1

def sample_lines(paths, step=0.5):
    """Points sampled along straight paths (used for 'nearest wall')."""
    pts = []
    for p in paths:
        for q in polylines(p):
            for a, b in zip(q, q[1:]):
                n = max(2, int(np.linalg.norm(b - a) / step))
                pts.append(np.linspace(a, b, n))
    return np.vstack(pts) if pts else np.zeros((0, 2))


def sample_lines_dir(paths, step=0.5):
    """Like sample_lines, plus the unit direction of the segment each point lies on."""
    pts, dirs = [], []
    for p in paths:
        for q in polylines(p):
            for a, b in zip(q, q[1:]):
                L = float(np.linalg.norm(b - a))
                if L < 1e-6: continue
                n = max(2, int(L / step))
                pts.append(np.linspace(a, b, n)); dirs.append(np.repeat([(b - a) / L], n, axis=0))
    if not pts: return np.zeros((0, 2)), np.zeros((0, 2))
    return np.vstack(pts), np.vstack(dirs)
