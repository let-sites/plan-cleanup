"""Tests on small made-up drawings (no customer files needed)."""
import math, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf, ezdxf
from planclean.profile import Profile, Settings
from planclean.teach import Teacher
from planclean.engine import clean_pdf, pens_in
from planclean.vec import in_region
from planclean import dxfout, dwgin

def outlet(page, x, y, vertical_wall=True):
    """Builder-style receptacle: circle with two parallel lines through it, drawn at width 0.36."""
    sh = page.new_shape(); sh.draw_circle((x, y), 5); sh.finish(width=0.36, color=(0, 0, 0))
    if vertical_wall:
        sh.draw_line((x - 9, y - 1.5), (x + 6, y - 1.5)); sh.draw_line((x - 9, y + 1.5), (x + 6, y + 1.5))
    else:
        sh.draw_line((x - 1.5, y - 9), (x - 1.5, y + 6)); sh.draw_line((x + 1.5, y - 9), (x + 1.5, y + 6))
    sh.finish(width=0.36, color=(0, 0, 0)); sh.commit()

def light(page, x, y):
    sh = page.new_shape(); sh.draw_circle((x, y), 5); sh.finish(width=0.36, color=(0, 0, 0))
    for dx, dy in ((1, 0), (0, 1)):
        sh.draw_line((x - 8 * dx, y - 8 * dy), (x + 8 * dx, y + 8 * dy))
    sh.finish(width=0.36, color=(0, 0, 0)); sh.commit()

def make_pdf(path):
    doc = pymupdf.open(); page = doc.new_page(width=600, height=600)
    sh = page.new_shape(); sh.draw_rect(pymupdf.Rect(100, 100, 500, 500)); sh.finish(width=0.96, color=(0, 0, 0)); sh.commit()
    outlet(page, 109, 200); outlet(page, 109, 350); outlet(page, 300, 109, vertical_wall=False)
    light(page, 300, 300)
    doc.save(path); return path

def test_pdf_teach_and_clean(tmp_path=None):
    tmp = tempfile.mkdtemp()
    doc = pymupdf.open(make_pdf(os.path.join(tmp, 'p.pdf'))); page = doc[0]
    region = (90, 90, 510, 510)
    prof = Profile(name='Test')
    paths = [p for p in page.get_drawings() if in_region(p, region)]
    for k in pens_in(paths):
        prof.pen_roles[k] = 'wall' if k.startswith('0.96') else 'electrical'
    t = Teacher(page, region, prof)
    ex, _ = t.example_at(109, 200, 'outlet'); prof.symbols.append(ex)
    ex, _ = t.example_at(300, 300, 'ceiling_light'); prof.symbols.append(ex)
    p = os.path.join(tmp, 'prof.json'); prof.save(p); prof = Profile.load(p)
    res = clean_pdf(page, region, prof)
    kinds = sorted(s.cls for s in res.symbols)
    assert kinds == ['ceiling_light', 'outlet', 'outlet', 'outlet'], kinds
    # outlets face into the room: left wall -> +x, top wall -> -y in page = +y in drawing
    left = [s for s in res.symbols if s.cls == 'outlet' and s.src[0] < 120]
    assert all(s.dx > 0.9 for s in left)
    top = [s for s in res.symbols if s.cls == 'outlet' and s.src[1] < 120][0]
    assert top.dy < -0.9  # page y grows downward; drawing y grows upward, top wall faces down into room
    # writing works without a library (placeholders)
    out = os.path.join(tmp, 'o.dxf')
    missing = dxfout.write_dxf(res, out, None, Settings().block_for)
    d = ezdxf.readfile(out)
    assert len(d.modelspace().query('INSERT')) == 4
    assert 'standard_outlet' in missing

def test_dwg_input():
    tmp = tempfile.mkdtemp()
    doc = ezdxf.new(); msp = doc.modelspace()
    doc.layers.add('A-WALL'); doc.layers.add('E-POWR'); doc.layers.add('DIMS')
    msp.add_lwpolyline([(0, 0), (240, 0), (240, 240), (0, 240)], close=True, dxfattribs={'layer': 'A-WALL'})
    b = doc.blocks.new('BLDR-RECEP'); b.add_circle((0, 0), 3)
    msp.add_blockref('BLDR-RECEP', (6, 120), dxfattribs={'layer': 'E-POWR'})
    msp.add_text('KITCHEN', height=9, dxfattribs={'layer': 'E-POWR'}).set_placement((120, 120))
    msp.add_text('GFI', height=4, dxfattribs={'layer': 'E-POWR'}).set_placement((12, 126))
    msp.add_line((0, -20), (240, -20), dxfattribs={'layer': 'DIMS'})
    p = os.path.join(tmp, 'b.dxf'); doc.saveas(p)
    d = dwgin.open_drawing(p)
    prof = Profile(name='T', dwg_block_map={'BLDR-RECEP': 'outlet'}, dwg_layer_roles={'A-WALL': 'wall', 'DIMS': 'remove', 'E-POWR': 'electrical'})
    res = dwgin.clean_dwg(d, (-10, -30, 250, 250), prof)
    assert [s.cls for s in res.symbols] == ['outlet']
    assert res.symbols[0].dx > 0.9
    assert [t.lines for t in res.texts if t.kind == 'room'] == [['KITCHEN']]
    assert 'GFI' in res.dropped_labels
    assert all(r != 'remove' for r, _, _ in res.lines)
