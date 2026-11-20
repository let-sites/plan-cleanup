"""Clean a builder plan that came as DWG/DXF: real layers, blocks and text make this simpler."""
from __future__ import annotations
import difflib, math, re
from collections import Counter
import numpy as np
import ezdxf
from ezdxf import recover, path as ezpath, bbox as ezbbox
from .profile import Profile, NOTE_WORDS
from .result import Result, Sym, Text
from .symbols import ORIENTED

def open_drawing(dxf_path):
    doc, _ = recover.readfile(dxf_path)
    return doc

def inventory(doc):
    """Layers and block names used in model space (for the setup screen)."""
    msp = doc.modelspace()
    layers = Counter(e.dxf.layer for e in msp)
    blocks = Counter(e.dxf.name for e in msp.query('INSERT'))
    return layers, blocks

def extents(doc):
    b = ezbbox.extents(doc.modelspace(), fast=True)
    return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) if b.has_data else (0, 0, 1, 1)

def _inside(e, region):
    try:
        b = ezbbox.extents([e], fast=True)
    except Exception:
        return False
    if not b.has_data: return False
    x0, y0, x1, y1 = region
    return b.extmin.x >= x0 and b.extmax.x <= x1 and b.extmin.y >= y0 and b.extmax.y <= y1

def _flatten(e):
    try:
        p = ezpath.make_path(e)
        return [[(v.x, v.y) for v in sp.flattening(0.5)] for sp in p.sub_paths()] if p.has_sub_paths else [[(v.x, v.y) for v in p.flattening(0.5)]]
    except Exception:
        return []

def clean_dwg(doc, region, profile: Profile, progress=lambda m: None) -> Result:
    """region is in drawing units (x0, y0, x1, y1). Output stays in the drawing's own units, moved to start at (0,0)."""
    x0, y0 = region[0], region[3]
    def tx(x, y): return (x - x0, y - y0)
    res = Result(); res.to_page = lambda X, Y: (X + x0, Y + y0)
    msp = doc.modelspace()
    lrole = lambda layer: profile.dwg_layer_roles.get(layer, 'misc')
    ents = [e for e in msp if _inside(e, region)]
    progress('Sorting %d objects' % len(ents))
    texts, inserts, removed = [], [], Counter()
    for e in ents:
        t = e.dxftype(); role = lrole(e.dxf.layer)
        if role == 'remove': removed['removed layers'] += 1; continue
        if t == 'DIMENSION' or t == 'LEADER' or t == 'MULTILEADER': removed['dimensions/leaders'] += 1; continue
        if t in ('TEXT', 'MTEXT', 'ATTRIB'):
            raw = e.plain_text() if t == 'MTEXT' else e.dxf.text
            lines = [l.strip() for l in re.split(r'[\n\r]+', raw) if l.strip()]
            if not lines: continue
            ins = e.dxf.insert
            h = e.dxf.char_height if t == 'MTEXT' else e.dxf.height
            try:
                b = ezbbox.extents([e], fast=True); cx, cy = b.center.x, b.center.y
            except Exception:
                cx, cy = ins.x, ins.y
            texts.append(dict(lines=lines, n=' '.join(lines).upper(), cx=cx, cy=cy, size=h)); continue
        if t == 'INSERT':
            cls = profile.dwg_block_map.get(e.dxf.name)
            if cls:
                inserts.append((cls, e)); continue
            for ve in e.virtual_entities():
                if lrole(ve.dxf.layer) == 'remove' or ve.dxftype() in ('TEXT', 'MTEXT', 'ATTDEF', 'ATTRIB'): continue
                for pts in _flatten(ve):
                    if len(pts) > 1: res.lines.append((role if role != 'electrical' else 'wiring', [tx(*p) for p in pts], False))
            continue
        filled = t in ('HATCH', 'SOLID')
        for pts in _flatten(e):
            if len(pts) > 1: res.lines.append(('wiring' if role == 'electrical' else role, [tx(*p) for p in pts], filled))

    walls = []
    for role, pts, f in res.lines:
        if role in ('wall', 'opening') and not f:
            for a, b in zip(pts, pts[1:]):
                n = max(2, int(math.dist(a, b) / 0.5)); walls.append(np.linspace(a, b, n))
    walls = np.vstack(walls) if walls else np.zeros((0, 2))

    for cls, e in inserts:
        X, Y = tx(e.dxf.insert.x, e.dxf.insert.y)
        s = Sym(cls=cls, x=X, y=Y, src=(e.dxf.insert.x, e.dxf.insert.y))
        if cls in ORIENTED and len(walls):
            d = np.hypot(walls[:, 0] - X, walls[:, 1] - Y); i = int(d.argmin())
            ux, uy = X - walls[i, 0], Y - walls[i, 1]; L = math.hypot(ux, uy) or 1
            s.dx, s.dy = ux / L, uy / L
            if d[i] > 24: s.review = 'No wall nearby to face'
        res.symbols.append(s)

    drops = profile.compiled_drop()
    for w in texts:
        n = w['n']
        if any(p.match(n.replace(' ', '')) or p.match(n) for p in drops):
            res.dropped_labels.append(n); continue
        X, Y = tx(w['cx'], w['cy'])
        m = difflib.get_close_matches(n, list(profile.room_names), n=1, cutoff=0.75)
        if m:
            res.texts.append(Text('room', [profile.room_names[m[0]]], X, Y))
        else:
            res.texts.append(Text('note', [' '.join(NOTE_WORDS.get(t, t) for t in l.upper().split()) for l in w['lines']], X, Y))
    res.stats = dict(removed=dict(removed), symbols=len(res.symbols), texts=len(texts))
    return res
