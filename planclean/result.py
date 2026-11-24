"""What a cleanup produces, in drawing units (inches, y up), ready to review and save."""
from __future__ import annotations
from dataclasses import dataclass, field
import copy, math

@dataclass
class Sym:
    cls: str
    x: float                 # symbol centre, drawing units
    y: float
    dx: float = 0.0          # unit vector pointing into the room (away from the wall)
    dy: float = 1.0
    score: float = 1.0
    mods: list = field(default_factory=list)
    review: str = ""         # why a person should look at it ('' = fine)
    src: tuple = ()          # where it came from on the source sheet (page points)
    wall: float | None = None   # distance from the centre back to the wall it sits on (drawing units)

@dataclass
class Text:
    kind: str                # 'room' | 'note' | 'title' (the sheet's title)
    lines: list
    x: float
    y: float
    review: str = ""

@dataclass
class Result:
    lines: list = field(default_factory=list)    # (role, [(x,y),...], filled)
    arcs: list = field(default_factory=list)     # (role, cx, cy, r, start_deg, end_deg), counter-clockwise
    symbols: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    dropped_labels: list = field(default_factory=list)
    original: list = field(default_factory=list) # builder geometry that was removed or redrawn, drawing units
    to_page: object = None                       # function drawing units -> page points (for the viewer)

    def review_items(self):
        return [s for s in self.symbols if s.review] + [t for t in self.texts if t.review]

def rotation_deg(s: Sym):
    return math.degrees(math.atan2(s.dy, s.dx)) - 90.0


TITLE_H = 9.0     # inches, the sheet title's text height


def sheet_layout(res: Result, title_h: float = TITLE_H) -> list:
    """The texts as they go in the saved drawing: the sheet title centred under the plan,
    with the scale line under it, as on a finished sheet. The preview keeps them in place."""
    texts = [copy.copy(t) for t in res.texts]
    titles = [t for t in texts if t.kind == 'title']
    if not titles: return texts
    pts = [v for _, q, _ in res.lines for v in q] + [(s.x, s.y) for s in res.symbols]
    if not pts: return texts
    xs = [v[0] for v in pts]; ys = [v[1] for v in pts]
    X = (min(xs) + max(xs)) / 2; top = min(ys) - 18.0
    for t in titles:
        th = title_h * 1.4 * len(t.lines)
        t.x, t.y = X, top - th / 2; top -= th + 4.0
    for t in texts:
        if t.kind == 'note' and t.lines and t.lines[0].startswith('SCALE'):
            t.x, t.y = X, top - 4.0; top -= 8.0
    return texts
