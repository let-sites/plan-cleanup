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
        for pl in polylines(p):
            q = ((pl - [x0, y0]) * scale * 16).round().astype(np.int32)
            if fill and len(q) > 2:
                cv2.fillPoly(img, [q], 255, cv2.LINE_AA, shift=4)
            cv2.polylines(img, [q], False, 255, thick, cv2.LINE_AA, shift=4)
    return img

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
