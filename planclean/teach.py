"""Teaching the tool a new builder: one click per symbol type."""
from __future__ import annotations
from . import detect
from .profile import Profile, SymbolExample, img_to_b64
from .symbols import ORIENTED
from .vec import in_region, pen_key

class Teacher:
    """Keeps the rendered symbol image for one sheet so clicks can be turned into examples."""
    def __init__(self, page, region, profile: Profile):
        self.page, self.region, self.profile = page, region, profile
        self.paths = [p for p in page.get_drawings() if in_region(p, region)]
        self.refresh()

    def refresh(self):
        role = lambda p: self.profile.pen_roles.get(pen_key(p), 'misc')
        sym = [p for p in self.paths if role(p) in ('electrical', 'misc', 'opening')]
        self.img = detect.symbol_image(sym, self.region)
        self.anchors = detect.anchors(sym)

    def example_at(self, x, y, cls, half=None):
        """Make an example from a click at page point (x, y). Snaps to the nearest symbol centre."""
        a = detect.nearest_anchor(self.anchors, x, y)
        if a:
            _, kind, cx, cy = a
        else:
            kind, cx, cy = 'scan', x, y
        if half is None:
            half = 5.0 if kind == 'glyph' else 8.0
        t = detect.cut_template(self.img, self.region, cx, cy, half)
        rots = [0, 1, 2, 3] if (cls in ORIENTED or kind == 'glyph') else [0]
        ex = SymbolExample(cls=cls, kind=kind, half=half, rotations=rots, template=img_to_b64(t),
                           threshold=0.7 if kind == 'glyph' else 0.6)
        return ex, (cx, cy)

    def pens_near(self, x, y, r=6):
        return {pen_key(p) for p in self.paths
                if abs((p['rect'].x0 + p['rect'].x1) / 2 - x) < r and abs((p['rect'].y0 + p['rect'].y1) / 2 - y) < r}
