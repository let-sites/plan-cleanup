"""Rebuild the builder's dashed wiring as solid lines and arcs.

Builders plot wiring as dashes: each dash is a separate short path. Here the dashes are
chained back together (end to start, continuing in the same direction) and each chain is
fitted with a straight line or a circular arc, which is how wiring is drawn in CAD.
"""
from __future__ import annotations
import math
import numpy as np


def pieces_of(polys, tol=0.05):
    """Join a path's consecutive segments into connected polylines."""
    out = []
    for q in polys:
        q = np.asarray(q, float)
        if len(q) < 2: continue
        if out and np.hypot(*(out[-1][-1] - q[0])) <= tol:
            out[-1] = np.vstack([out[-1], q[1:]])
        else:
            out.append(q)
    return out


def _length(p):
    return float(np.sum(np.hypot(*np.diff(p, axis=0).T))) if len(p) > 1 else 0.0


def _dir(p, at_end):
    """Unit direction leaving the piece at one end (pointing outward)."""
    if at_end:
        a, b = p[max(0, len(p) - 3)], p[-1]
    else:
        a, b = p[min(len(p) - 1, 2)], p[0]
    v = b - a; n = math.hypot(*v)
    return v / n if n > 1e-9 else np.array([0.0, 0.0])


def chain(pieces, gap=7.5, max_turn=40.0):
    """Group pieces into ordered chains. Returns lists of oriented polylines.
    The dots of dash-dot lines are skipped so the dashes on either side join up."""
    pieces = [p for p in pieces if _length(p) >= 1.2]
    n = len(pieces)
    if n == 0: return []
    ends = []                                   # (piece, is_end, point, outward dir)
    for i, p in enumerate(pieces):
        ends.append((i, False, p[0], _dir(p, False)))
        ends.append((i, True, p[-1], _dir(p, True)))
    pts = np.array([e[2] for e in ends])
    cos_max = math.cos(math.radians(max_turn))
    cands = []
    for a in range(len(ends)):
        d = np.hypot(pts[:, 0] - pts[a, 0], pts[:, 1] - pts[a, 1])
        for b in np.nonzero(d <= gap)[0]:
            if b <= a or ends[b][0] == ends[a][0]: continue
            da, db = ends[a][3], ends[b][3]
            if np.dot(da, -db) < cos_max: continue              # must keep going the same way
            g = pts[b] - pts[a]; gl = math.hypot(*g)
            if gl > 0.3 and np.dot(g / gl, da) < cos_max: continue   # the gap must lie along the line
            cands.append((d[b] + 2.0 * (1 - np.dot(da, -db)), a, b))
    cands.sort()
    link = {}
    for _, a, b in cands:
        if a in link or b in link: continue
        link[a] = b; link[b] = a
    # endpoint id e = 2*piece + (1 for its last point, 0 for its first)
    seen = set(); chains = []
    order = sorted(range(n), key=lambda i: ((2 * i) in link) + ((2 * i + 1) in link))   # chain ends first
    for i in order:
        if i in seen: continue
        # enter piece i through a free endpoint if it has one
        enter = 2 * i if (2 * i) not in link else 2 * i + 1
        seq = []; p, e_in = i, enter
        while p not in seen:
            seen.add(p)
            seq.append(pieces[p] if e_in % 2 == 0 else pieces[p][::-1])
            e_out = e_in ^ 1
            if e_out not in link: break
            nxt = link[e_out]; p, e_in = nxt // 2, nxt
        chains.append(seq)
    return chains


def fit(points):
    """('line', p0, p1) | ('arc', cx, cy, r, a0, a1) with a0->a1 counter-clockwise | ('poly', pts)."""
    P = np.asarray(points, float)
    if len(P) < 2: return None
    p0, p1 = P[0], P[-1]
    chord = p1 - p0; L = math.hypot(*chord)
    if L > 1e-6:
        nrm = np.array([-chord[1], chord[0]]) / L
        dev = np.abs((P - p0) @ nrm)
        if dev.max() <= max(0.5, 0.008 * L):
            return ('line', tuple(p0), tuple(p1))
    # algebraic circle fit
    x, y = P[:, 0], P[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(P))]
    try:
        c, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    except np.linalg.LinAlgError:
        return ('poly', P)
    cx, cy = c[0], c[1]; r = math.sqrt(max(c[2] + cx * cx + cy * cy, 0))
    rms = float(np.sqrt(np.mean((np.hypot(x - cx, y - cy) - r) ** 2)))
    if r <= 0 or r > 50 * max(L, 1) or rms > max(0.6, 0.012 * r):
        return ('poly', P)
    ang = np.unwrap(np.arctan2(y - cy, x - cx))
    sweep = ang[-1] - ang[0]
    if abs(sweep) > math.radians(340): return ('poly', P)
    a0, a1 = math.degrees(ang[0]), math.degrees(ang[-1])
    if sweep < 0: a0, a1 = a1, a0
    return ('arc', cx, cy, r, a0 % 360, a1 % 360)


def arc_points(cx, cy, r, a0, a1, step_deg=3.0):
    sweep = (a1 - a0) % 360 or 360
    n = max(2, int(sweep / step_deg) + 1)
    t = np.radians(a0 + np.linspace(0, sweep, n))
    return np.c_[cx + r * np.cos(t), cy + r * np.sin(t)]


def merge_arcs(arcs, gap=10.0):
    """Join arcs that lie on the same circle and nearly touch. arcs: (role, cx, cy, r, a0, a1)."""
    arcs = list(arcs); changed = True
    def span(a): return (a[5] - a[4]) % 360 or 360
    while changed:
        changed = False
        for i in range(len(arcs)):
            for j in range(i + 1, len(arcs)):
                A, B = arcs[i], arcs[j]
                if A[0] != B[0]: continue
                r = (A[3] + B[3]) / 2
                if abs(A[3] - B[3]) > max(1.0, 0.03 * r) or math.hypot(A[1] - B[1], A[2] - B[2]) > max(1.5, 0.04 * r): continue
                g = math.degrees(gap / r)
                # B continues A (A end -> B start) or A continues B
                for X, Y in ((A, B), (B, A)):
                    d = (Y[4] - X[5]) % 360
                    if d <= g or d >= 360 - min(span(X), span(Y)):
                        new_span = (Y[5] - X[4]) % 360 or 360
                        if new_span >= span(X) and new_span >= span(Y) and new_span < 345:
                            arcs[i] = (A[0], (A[1] + B[1]) / 2, (A[2] + B[2]) / 2, r, X[4], Y[5]); arcs.pop(j)
                            changed = True; break
                if changed: break
            if changed: break
    return arcs


def extend_to_symbols(arcs, sym_xy, reach, stop):
    """Run each arc end on to a symbol that sits on the same circle just past the end.
    sym_xy: Nx2 symbol centres; reach: how far past the end to look; stop: gap left at the symbol."""
    if len(sym_xy) == 0: return arcs
    out = []
    for role, cx, cy, r, a0, a1 in arcs:
        ang = np.degrees(np.arctan2(sym_xy[:, 1] - cy, sym_xy[:, 0] - cx))
        on = np.abs(np.hypot(sym_xy[:, 0] - cx, sym_xy[:, 1] - cy) - r) <= max(stop * 1.5, 0.02 * r)
        lim = math.degrees(reach / r); gap = math.degrees(stop / r)
        span = (a1 - a0) % 360
        e1 = [((a - a1) % 360) for a, o in zip(ang, on) if o and 0 < (a - a1) % 360 <= lim]
        e0 = [((a0 - a) % 360) for a, o in zip(ang, on) if o and 0 < (a0 - a) % 360 <= lim]
        if e1 and min(e1) > gap: a1 = (a1 + min(e1) - gap) % 360
        if e0 and min(e0) > gap and span + min(e0) < 340: a0 = (a0 - min(e0) + gap) % 360
        out.append((role, cx, cy, r, a0, a1))
    return out
