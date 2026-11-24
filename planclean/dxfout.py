"""Write a Result to DXF using the blocks from your own library drawing."""
from __future__ import annotations
import math
import ezdxf
from ezdxf.addons import Importer
from ezdxf.enums import TextEntityAlignment
from .result import Result, rotation_deg, sheet_layout
from .symbols import ORIENTED

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
              text_layer='$Circuits', symbol_layer='$Standard', text_style='Notes', text_title=9.0, layout=True):
    """layout=False: the texts are already placed (a combined Plan)."""
    out = ezdxf.new('R2018', setup=True)
    out.header['$INSUNITS'] = 1  # inches
    for name, col in LAYERS:
        out.layers.add(name, color=col)
    # your own layers; when pasted into a template, the template's layer settings win
    if text_layer not in out.layers: out.layers.add(text_layer, color=1)
    if symbol_layer not in out.layers: out.layers.add(symbol_layer, color=7)
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
    msp = out.modelspace()
    def block_of(s):
        n = block_for(s.cls); return out.blocks.get(n) if n else None
    wire_lines, wire_arcs = retarget_wires(res, block_of)
    for role, pts, filled in wire_lines:
        lay = ROLE_LAYER.get(role, 'BASE-MISC')
        if len(pts) == 2:
            msp.add_line(pts[0], pts[1], dxfattribs={'layer': lay})
        elif len(pts) > 2:
            msp.add_lwpolyline(pts, dxfattribs={'layer': lay})
            if filled:
                h = msp.add_hatch(color=7, dxfattribs={'layer': lay}); h.paths.add_polyline_path(pts, is_closed=True)
    for role, cx, cy, r, a0, a1 in wire_arcs:
        msp.add_arc((cx, cy), r, a0, a1, dxfattribs={'layer': ROLE_LAYER.get(role, 'BASE-MISC')})
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
        if s.review:
            msp.add_circle((s.x, s.y), 12, dxfattribs={'layer': 'REVIEW'})
    for t in (sheet_layout(res, text_title) if layout else res.texts):
        if t.kind in ('room', 'title'):
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style,
                                                           'char_height': text_title if t.kind == 'title' else text_room, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        else:
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style, 'char_height': text_note, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        if t.review:
            msp.add_circle((t.x, t.y), 14, dxfattribs={'layer': 'REVIEW'})
    out.saveas(path)
    return sorted(m for m in missing if m)
