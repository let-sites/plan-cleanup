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

def _placeholders(doc):
    if 'SMOKE_CO' not in doc.blocks:
        b = doc.blocks.new('SMOKE_CO')
        b.add_lwpolyline([(-3, -3), (3, -3), (3, 3), (-3, 3)], close=True)
        b.add_line((-3, -3), (3, 3)); b.add_line((-3, 3), (3, -3))
    if 'SMOKE' not in doc.blocks:
        b = doc.blocks.new('SMOKE'); b.add_circle((0, 0), 3); b.add_circle((0, 0), 1.2)
    if 'UNMAPPED' not in doc.blocks:
        b = doc.blocks.new('UNMAPPED'); b.add_circle((0, 0), 3); b.add_line((-3, 0), (3, 0)); b.add_line((0, -3), (0, 3))

def write_dxf(res: Result, path, library_doc, block_for, text_room=9.0, text_note=5.0):
    out = ezdxf.new('R2018', setup=True)
    out.header['$INSUNITS'] = 1  # inches
    for name, col in LAYERS:
        out.layers.add(name, color=col)
    wanted = sorted({block_for(s.cls) for s in res.symbols if block_for(s.cls)})
    have = [n for n in wanted if library_doc is not None and library_doc.blocks.get(n) is not None]
    if have:
        imp = Importer(library_doc, out); imp.import_blocks(have); imp.finalize()
    for b in out.blocks:
        for e in b:
            if not e.dxf.hasattr('layer') or not e.dxf.layer: e.dxf.layer = '0'
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
    missing = set()
    for s in res.symbols:
        if s.cls == 'remove': continue
        name = block_for(s.cls)
        if not name or out.blocks.get(name) is None:
            missing.add(name or s.cls); name = 'UNMAPPED'
        x, y, rot = s.x, s.y, 0.0
        if s.cls in ORIENTED:
            rot = rotation_deg(s)
            off = block_offset(out.blocks.get(name))
            x -= s.dx * off; y -= s.dy * off
        msp.add_blockref(name, (x, y), dxfattribs={'layer': 'ELEC-SYMBOLS', 'rotation': rot})
        if s.review:
            msp.add_circle((s.x, s.y), 12, dxfattribs={'layer': 'REVIEW'})
    for t in res.texts:
        if t.kind == 'room':
            msp.add_text(t.lines[0], height=text_room, dxfattribs={'layer': 'ELEC-TEXT'}).set_placement(
                (t.x, t.y), align=TextEntityAlignment.MIDDLE_CENTER)
        else:
            msp.add_mtext('\\P'.join(t.lines), dxfattribs={'layer': 'ELEC-TEXT', 'char_height': text_note,
                                                           'insert': (t.x, t.y), 'attachment_point': 5})
        if t.review:
            msp.add_circle((t.x, t.y), 14, dxfattribs={'layer': 'REVIEW'})
    out.saveas(path)
    return sorted(m for m in missing if m)
