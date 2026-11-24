"""Write a Result to DXF using the blocks from your own library drawing."""
from __future__ import annotations
import math
import ezdxf
from ezdxf.addons import Importer
from ezdxf.enums import TextEntityAlignment
from .result import Result, rotation_deg, sheet_layout
from .symbols import ORIENTED, label as sym_label

LAYERS = [('BASE-WALLS', 7), ('BASE-DOORS-WINDOWS', 8), ('BASE-CABINETS', 8), ('BASE-MISC', 8),
          ('ELEC-WIRING', 7), ('ELEC-SYMBOLS', 7), ('ELEC-TEXT', 7), ('REVIEW', 1)]
ROLE_LAYER = {'wall': 'BASE-WALLS', 'opening': 'BASE-DOORS-WINDOWS', 'casework': 'BASE-CABINETS',
              'misc': 'BASE-MISC', 'wiring': 'ELEC-WIRING'}

def load_library(path):
    from ezdxf import recover
    doc, _ = recover.readfile(path)
    _recentre_far_blocks(doc)
    return doc

def _recentre_far_blocks(doc):
    """A block whose drawing sits far from its base point (base point never set) is moved so
    its middle is on the base point; otherwise it would land hundreds of feet away."""
    from ezdxf import bbox
    for b in doc.blocks:
        if b.name.startswith('*') or not len(b): continue
        try: ext = bbox.extents(b, fast=True)
        except Exception: continue
        if not ext.has_data: continue
        c = ext.center
        if math.hypot(c.x, c.y) > 100 and max(ext.size.x, ext.size.y) < 200:
            for e in b:
                try: e.translate(-c.x, -c.y, 0)
                except Exception: pass

def wire_point(blk):
    """Where wires end on this block: the POINT you put in it, in block units, or None."""
    if blk is None: return None
    for e in blk:
        if e.dxftype() == 'POINT':
            p = e.dxf.location; return (p.x, p.y)
    return None

def retarget_wires(res, block_of):
    """Lines and arcs of the result with wire ends moved from symbol centres to each block's wire POINT."""
    from .wiring import arc_through
    moves = {}
    for s in res.symbols:
        blk = block_of(s); pt = wire_point(blk)
        if pt is None: continue
        x, y, rot = placement(s, blk); c, si = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        moves[(s.x, s.y)] = (x + pt[0] * c - pt[1] * si, y + pt[0] * si + pt[1] * c)
    if not moves: return list(res.lines), list(getattr(res, 'arcs', []))
    keys = list(moves)
    def move(p):
        for k in keys:
            if abs(k[0] - p[0]) < 0.5 and abs(k[1] - p[1]) < 0.5: return moves[k]
        return p
    lines = []
    for role, pts, filled in res.lines:
        if role == 'wiring' and len(pts) >= 2:
            pts = [move(pts[0])] + list(pts[1:-1]) + [move(pts[-1])]
        lines.append((role, pts, filled))
    arcs = []
    for role, cx, cy, r, a0, a1 in getattr(res, 'arcs', []):
        p0 = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
        p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
        q0, q1 = move(p0), move(p1)
        if q0 == p0 and q1 == p1: arcs.append((role, cx, cy, r, a0, a1)); continue
        am = math.radians(a0 + ((a1 - a0) % 360) / 2)
        f = arc_through(q0, (cx + r * math.cos(am), cy + r * math.sin(am)), q1)
        if f[0] == 'arc': arcs.append((role,) + tuple(float(v) for v in f[1:]))
        else: lines.append((role, [f[1], f[2]], False))
    return lines, arcs

def block_offset(blk):
    """Distance from a block's insert point (on the wall) to the symbol centre, along +Y."""
    best = None
    for e in blk:
        if e.dxftype() == 'CIRCLE':
            c = e.dxf.center
            d = math.hypot(c.x, c.y)
            if d > 0.5 and (best is None or e.dxf.radius > best[1]): best = (c.y, e.dxf.radius)
    if best: return best[0]
    ys = []
    for e in blk:
        if e.dxftype() == 'LINE': ys += [e.dxf.start.y, e.dxf.end.y]
    return (min(ys) + max(ys)) / 2 if ys else 0.0

def placement(s, blk):
    """Insert point and rotation for a symbol's block (oriented blocks sit on the wall, facing the room)."""
    x, y, rot = s.x, s.y, 0.0
    if s.cls in ORIENTED and blk is not None:
        rot = rotation_deg(s)
        # your blocks have their insert point on the wall: put it on the wall line the symbol sits on
        off = s.wall if getattr(s, 'wall', None) is not None else block_offset(blk)
        x -= s.dx * off; y -= s.dy * off
    else:
        rot = getattr(s, 'turn', 0.0) or 0.0
    return x, y, rot

def _placeholders(doc):
    if 'SMOKE_CO' not in doc.blocks:
        b = doc.blocks.new('SMOKE_CO')
        b.add_lwpolyline([(-3, -3), (3, -3), (3, 3), (-3, 3)], close=True)
        b.add_line((-3, -3), (3, 3)); b.add_line((-3, 3), (3, -3))
    if 'SMOKE' not in doc.blocks:
        b = doc.blocks.new('SMOKE'); b.add_circle((0, 0), 3); b.add_circle((0, 0), 1.2)
    if 'UNMAPPED' not in doc.blocks:
        b = doc.blocks.new('UNMAPPED'); b.add_circle((0, 0), 3); b.add_line((-3, 0), (3, 0)); b.add_line((0, -3), (0, 3))

def write_dxf(res: Result, path, library_doc, block_for, text_room=3.0, text_note=3.0,
              text_layer='$RoomNames', symbol_layer='$Standard', text_style='Notes', text_title=9.0, layout=True, base=None, clear=True):
    """layout=False: the texts are already placed (a combined Plan).
    base: a drawing to add to (your master template); the plan goes in empty model space above what's there."""
    out = base if base is not None else ezdxf.new('R2018', setup=True)
    if base is not None:
        if clear: res = _moved_clear_of(res, out)
    else:
        out.header['$INSUNITS'] = 1  # inches
    for name, col in LAYERS:
        if name not in out.layers: out.layers.add(name, color=col)
    out.layers.get('REVIEW').dxf.plot = 0        # the red check-me circles show on screen but never print
    # your own layers; when pasted into a template, the template's layer settings win
    if text_layer not in out.layers: out.layers.add(text_layer, color=7)
    if symbol_layer not in out.layers: out.layers.add(symbol_layer, color=7)
    _drop_layer(out, '$ELEC', symbol_layer)             # no longer used: anything on it moves to the symbol layer
    if text_style not in out.styles: out.styles.add(text_style, font='arial.ttf')
    wanted = sorted({block_for(s.cls) for s in res.symbols if block_for(s.cls)})
    have = [n for n in wanted if library_doc is not None and library_doc.blocks.get(n) is not None]
    if have:
        imp = Importer(library_doc, out); imp.import_blocks(have); imp.finalize()
    for b in out.blocks:
        imported = b.name in have
        for e in b:
            if not e.dxf.hasattr('layer') or not e.dxf.layer: e.dxf.layer = '0'
            if imported and e.dxftype() == 'POINT':
                e.dxf.layer = 'Defpoints'; continue
            if imported:
                # your blocks live on $Standard (layer 0 would show in every viewport), coloured ByLayer
                e.dxf.layer = symbol_layer
                if e.dxf.is_supported('color'): e.dxf.color = 256    # BYLAYER
                if e.dxf.is_supported('true_color') and e.dxf.hasattr('true_color'): e.dxf.discard('true_color')
            if e.dxf.hasattr('style') and e.dxf.style not in out.styles: e.dxf.style = 'Standard'
    _placeholders(out)
    msp = out.modelspace(); checks = []
    def block_of(s):
        n = block_for(s.cls); return out.blocks.get(n) if n else None
    wire_lines, wire_arcs = retarget_wires(res, block_of)
    def layer_of(role):          # the wires go with the symbols, on your symbol layer ($Standard)
        return symbol_layer if role == 'wiring' else ROLE_LAYER.get(role, 'BASE-MISC')
    for role, pts, filled in wire_lines:
        lay = layer_of(role)
        if len(pts) == 2:
            msp.add_line(pts[0], pts[1], dxfattribs={'layer': lay})
        elif len(pts) > 2:
            msp.add_lwpolyline(pts, dxfattribs={'layer': lay})
            if filled:
                h = msp.add_hatch(color=7, dxfattribs={'layer': lay}); h.paths.add_polyline_path(pts, is_closed=True)
    for role, cx, cy, r, a0, a1 in wire_arcs:
        msp.add_arc((cx, cy), r, a0, a1, dxfattribs={'layer': layer_of(role)})
    missing = set()
    for s in res.symbols:
        if s.cls == 'remove': continue
        name = block_for(s.cls)
        if not name or out.blocks.get(name) is None:
            missing.add(name or s.cls); name = 'UNMAPPED'
        x, y, rot = placement(s, out.blocks.get(name))
        ref = msp.add_blockref(name, (x, y), dxfattribs={'layer': symbol_layer, 'rotation': rot})
        try:
            if any(e.dxftype() == 'ATTDEF' for e in out.blocks.get(name)): ref.add_auto_attribs({})   # e.g. the 4" on a can
        except Exception: pass
        if s.review or getattr(s, 'added', False):
            what = sym_label(s.cls).split(' (')[0]
            checks.append((s.x, s.y, 12, f'{what}: added by hand, draw its wire' if getattr(s, 'added', False) else f'{what}: {s.review}'))
    for t in (sheet_layout(res, text_title) if layout else res.texts):
        if t.kind in ('room', 'title'):
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style,
                                                           'char_height': text_title if t.kind == 'title' else text_room, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        else:
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style, 'char_height': text_note, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        if t.review:
            checks.append((t.x, t.y, 14, f'Note "{" ".join(t.lines)}": {t.review}'))
    # a numbered list of what to double-check, beside the plan, with the same number on the spot (never prints)
    if checks:
        xs = [p[0] for _, pts, _ in res.lines for p in pts] or [c[0] for c in checks]
        ys = [p[1] for _, pts, _ in res.lines for p in pts] or [c[1] for c in checks]
        lx, ly = max(xs) + 36, max(ys)
        msp.add_text('CHECK (does not print)', dxfattribs={'layer': 'REVIEW', 'height': 6.0, 'insert': (lx, ly)})
        for n, (x, y, r, text) in enumerate(checks, 1):
            msp.add_circle((x, y), r, dxfattribs={'layer': 'REVIEW'})
            msp.add_text(str(n), dxfattribs={'layer': 'REVIEW', 'height': 5.0, 'insert': (x + r + 2, y + r - 4)})
            msp.add_text(f'{n}. {text}', dxfattribs={'layer': 'REVIEW', 'height': 4.0, 'insert': (lx, ly - 10 - 7 * n)})
    out.saveas(path)
    return sorted(m for m in missing if m)


def _moved_clear_of(res, doc, gap=240.0):
    """The result moved so it sits above everything already in the drawing's model space, left edges lined up."""
    from ezdxf import bbox
    import copy
    try: ext = bbox.extents(doc.modelspace(), fast=True)
    except Exception: ext = None
    if ext is None or not ext.has_data: return res
    xs = [x for _, q, _ in res.lines for x, _ in q] + [s.x for s in res.symbols] + [t.x for t in res.texts]
    ys = [y for _, q, _ in res.lines for _, y in q] + [s.y for s in res.symbols] + [t.y for t in res.texts]
    if not xs: return res
    dx = ext.extmin.x - min(xs); dy = ext.extmax.y + gap - min(ys)
    out = copy.copy(res)
    out.lines = [(r, [(x + dx, y + dy) for x, y in q], f) for r, q, f in res.lines]
    out.arcs = [(r, cx + dx, cy + dy, rr, a0, a1) for r, cx, cy, rr, a0, a1 in res.arcs]
    out.symbols = []
    for s in res.symbols:
        s = copy.copy(s); s.x += dx; s.y += dy; out.symbols.append(s)
    out.texts = []
    for t in res.texts:
        t = copy.copy(t); t.x += dx; t.y += dy; out.texts.append(t)
    return out


def open_template(path, exe):
    """Your master template, read fresh from disk."""
    from ezdxf import recover
    from . import oda
    doc, _ = recover.readfile(oda.to_dxf(path, exe))
    return doc


def blank_like(template):
    """A new, empty drawing with the template's layers, text styles, dimension styles and units, but none of its drawings."""
    out = ezdxf.new(template.dxfversion if template.dxfversion >= 'AC1027' else 'R2018')
    imp = Importer(template, out)
    imp.import_tables(('layers', 'linetypes', 'styles', 'dimstyles'))
    imp.finalize()
    for k in ('$INSUNITS', '$LUNITS', '$LUPREC', '$DIMSCALE', '$LTSCALE', '$DIMSTYLE', '$TEXTSTYLE'):
        try: out.header[k] = template.header[k]
        except Exception: pass
    return out


def _drop_layer(doc, name, move_to):
    """Delete a layer from the drawing; anything still on it moves to another layer first."""
    if name not in doc.layers: return
    for e in list(doc.entitydb.values()):
        try:
            if e.dxf.is_supported('layer') and e.dxf.get('layer', '').lower() == name.lower(): e.dxf.layer = move_to
        except Exception: pass
    try: doc.layers.remove(name)
    except Exception: pass
