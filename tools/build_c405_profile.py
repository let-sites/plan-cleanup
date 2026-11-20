"""Build the C405 builder profile the way a user would (pen roles + one click per symbol), run it."""
import sys, json, time, pymupdf
from planclean.profile import Profile
from planclean.teach import Teacher
from planclean.engine import clean_pdf, pens_in
from planclean.dxfout import write_dxf, load_library
from planclean.profile import Settings
from planclean.vec import in_region

pdf, lib, out = sys.argv[1], sys.argv[2], sys.argv[3]
doc = pymupdf.open(pdf); page = doc[35]
region = (110, 1130, 905, 2195)
prof = Profile(name='American Legend')
paths = [p for p in page.get_drawings() if in_region(p, region)]
for k, v in pens_in(paths).items():
    w = float(k.split()[0]) if not k.startswith('fill') else None
    prof.pen_roles[k] = ('wall' if k.startswith('fill') or w == 0.96 else
                         'electrical' if w == 0.36 and k.endswith('#000000') else
                         'opening' if w == 0.24 else 'casework' if w == 0.6 else 'misc')
t = Teacher(page, region, prof)
clicks = {'outlet': (178.1, 1507.8), 'ceiling_light': (576.6, 1305.3), 'recessed_can': (500.7, 1413.3),
          'smoke': (355.0, 1874.1), 'co_smoke': (719.7, 1361.8), 'exhaust_fan': (836.9, 1215.3),
          'wall_light': (892.8, 1570.0), 'ceiling_outlet': (592.3, 1923.1), 'switch': (487.2, 1328.0)}
for cls, (x, y) in clicks.items():
    ex, c = t.example_at(x, y, cls); prof.symbols.append(ex); print(cls, ex.kind, [round(v, 1) for v in c])
prof.save(out + '.profile.json')
t0 = time.time()
res = clean_pdf(page, region, Profile.load(out + '.profile.json'), exclude=[(100, 2110, 175, 2200)], progress=print)
print('seconds', round(time.time() - t0, 1))
from collections import Counter
print(Counter(s.cls for s in res.symbols), res.stats)
print('rooms', [x.lines for x in res.texts if x.kind == 'room'])
print('notes', [x.lines for x in res.texts if x.kind == 'note'])
print('review', [(s.cls, s.review) for s in res.review_items() if hasattr(s, 'cls')])
st = Settings()
missing = write_dxf(res, out, load_library(lib), st.block_for)
print('missing blocks', missing)
