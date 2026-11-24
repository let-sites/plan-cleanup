"""Teaching the tool a new builder: one click per symbol type."""
from __future__ import annotations
from . import detect
from .profile import Profile, SymbolExample, img_to_b64
from .symbols import ORIENTED
from .vec import in_region, pen_key, page_text, text_anchors

def _wire_like(p):
    """A loose stroke (a piece of wire or a leader), not part of a symbol's outline: one open line or curve."""
    it = p.get('items') or []
    return len(it) <= 2 and not p.get('fill') and all(i[0] in ('l', 'c') for i in it) and \
        max(p['rect'].width, p['rect'].height) > 6 and min(p['rect'].width, p['rect'].height) < 0.6 * max(p['rect'].width, p['rect'].height)


class Teacher:
    """Keeps the rendered symbol image for one sheet so clicks can be turned into examples."""
    def __init__(self, page, region, profile: Profile):
        self.page, self.region, self.profile = page, region, profile
        self.paths = [p for p in page.get_drawings() if in_region(p, region)]
        self.texts = page_text(page, region)
        self.refresh()

    def refresh(self):
        role = lambda p: self.profile.pen_roles.get(pen_key(p), 'misc')
        sym = [p for p in self.paths if role(p) in ('electrical', 'misc', 'opening')]
        self.img = detect.symbol_image(sym, self.region, self.texts)
        self.anchors = detect.anchors(sym) + text_anchors(self.texts)

    def example_at(self, x, y, cls, half=None, snap=True):
        """Make an example from a click at page point (x, y). Snaps to the nearest symbol centre (snap=False: exactly there)."""
        a = detect.nearest_anchor(self.anchors, x, y) if snap else None
        if a:
            _, kind, cx, cy = a
        else:
            kind, cx, cy = 'scan', x, y
        if not snap:
            near = detect.nearest_anchor(self.anchors, x, y)
            if near: kind = near[1]                 # same kind of symbol, just centred where you put it
        if half is None:
            half = max(5.0 if kind == 'glyph' else 8.0, self.symbol_half(cx, cy))   # the whole symbol, not just its middle
        t = detect.cut_template(self.img, self.region, cx, cy, half)
        rots = [0, 1, 2, 3] if (cls in ORIENTED or kind == 'glyph') else [0]
        ex = SymbolExample(cls=cls, kind=kind, half=half, rotations=rots, template=img_to_b64(t),
                           threshold=0.7 if kind == 'glyph' else 0.6)
        return ex, (cx, cy)

    def pens_near(self, x, y, r=6):
        return {pen_key(p) for p in self.paths
                if abs((p['rect'].x0 + p['rect'].x1) / 2 - x) < r and abs((p['rect'].y0 + p['rect'].y1) / 2 - y) < r}

    def symbol_half(self, cx, cy, reach=2.0, most=14.0):
        """Half the size of the symbol drawn around (cx, cy): the small electrical shapes that reach near it,
        grown through what touches them."""
        el = [p['rect'] for p in self.paths if self.profile.pen_roles.get(pen_key(p)) == 'electrical'
              and max(p['rect'].width, p['rect'].height) < 2 * most and not _wire_like(p)]
        box = None
        for r in el:
            if r.x0 - reach <= cx <= r.x1 + reach and r.y0 - reach <= cy <= r.y1 + reach:
                box = r if box is None else box | r
        if box is None: return 0.0
        grown = True
        for _ in range(3):
            if not grown: break
            grown = False
            for r in el:
                if not (box.contains(r)) and r.intersects(box + (-1, -1, 1, 1)):
                    nb = box | r
                    if max(nb.x1 - cx, cx - nb.x0, nb.y1 - cy, cy - nb.y0) <= most: box = nb; grown = True
        return min(most, max(box.x1 - cx, cx - box.x0, box.y1 - cy, cy - box.y0) + 1.5)
