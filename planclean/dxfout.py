"""Write a Result to DXF using the blocks from your own library drawing."""
from __future__ import annotations
import math
import ezdxf
from ezdxf.addons import Importer
from ezdxf.enums import TextEntityAlignment
from .result import Result, rotation_deg
from .symbols import ORIENTED

LAYERS = [('BASE-WALLS', 7), ('BASE-DOORS-WINDOWS', 8), ('BASE-CABINETS', 8), ('BASE-MISC', 8),
          ('ELEC-WIRING', 7), ('ELEC-SYMBOLS', 7), ('ELEC-TEXT', 7), ('REVIEW', 1)]
ROLE_LAYER = {'wall': 'BASE-WALLS', 'opening': 'BASE-DOORS-WINDOWS', 'casework': 'BASE-CABINETS',
              'misc': 'BASE-MISC', 'wiring': 'ELEC-WIRING'}

def load_library(path):
    from ezdxf import recover
    doc, _ = recover.readfile(path)
    return doc

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
              text_layer='$Circuits', symbol_layer='$Standard', text_style='Notes'):
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
            if imported:
                # the block takes the look of the layer it is placed on ($Standard), like a block made on layer 0
                e.dxf.layer = '0'
                if e.dxf.is_supported('color'): e.dxf.color = 0      # BYBLOCK
                if e.dxf.is_supported('true_color') and e.dxf.hasattr('true_color'): e.dxf.discard('true_color')
            if e.dxf.hasattr('style') and e.dxf.style not in out.styles: e.dxf.style = 'Standard'
    _placeholders(out)
    msp = out.modelspace()
    for role, pts, filled in res.lines:
        lay = ROLE_LAYER.get(role, 'BASE-MISC')
        if len(pts) == 2:
            msp.add_line(pts[0], pts[1], dxfattribs={'layer': lay})
        elif len(pts) > 2:
            msp.add_lwpolyline(pts, dxfattribs={'layer': lay})
            if filled:
                h = msp.add_hatch(color=7, dxfattribs={'layer': lay}); h.paths.add_polyline_path(pts, is_closed=True)
    for role, cx, cy, r, a0, a1 in getattr(res, 'arcs', []):
        msp.add_arc((cx, cy), r, a0, a1, dxfattribs={'layer': ROLE_LAYER.get(role, 'BASE-MISC')})
    missing = set()
    for s in res.symbols:
        if s.cls == 'remove': continue
        name = block_for(s.cls)
        if not name or out.blocks.get(name) is None:
            missing.add(name or s.cls); name = 'UNMAPPED'
        x, y, rot = placement(s, out.blocks.get(name))
        msp.add_blockref(name, (x, y), dxfattribs={'layer': symbol_layer, 'rotation': rot})
        if s.review:
            msp.add_circle((s.x, s.y), 12, dxfattribs={'layer': 'REVIEW'})
    for t in res.texts:
        if t.kind == 'room':
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style, 'char_height': text_room, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        else:
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': text_layer, 'style': text_style, 'char_height': text_note, 'color': 7,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        if t.review:
            msp.add_circle((t.x, t.y), 14, dxfattribs={'layer': 'REVIEW'})
    out.saveas(path)
    return sorted(m for m in missing if m)
