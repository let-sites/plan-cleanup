"""What a cleanup produces, in drawing units (inches, y up), ready to review and save."""
from __future__ import annotations
from dataclasses import dataclass, field
import math

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

@dataclass
class Text:
    kind: str                # 'room' | 'note'
    lines: list
    x: float
    y: float
    review: str = ""

@dataclass
class Result:
    lines: list = field(default_factory=list)    # (role, [(x,y),...], filled)
    symbols: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    dropped_labels: list = field(default_factory=list)
    to_page: object = None                       # function drawing units -> page points (for the viewer)

    def review_items(self):
        return [s for s in self.symbols if s.review] + [t for t in self.texts if t.review]

def rotation_deg(s: Sym):
    return math.degrees(math.atan2(s.dy, s.dx)) - 90.0
