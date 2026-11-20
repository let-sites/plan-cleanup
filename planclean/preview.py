"""Outlines of library blocks, so the review screen can show the real symbols."""
from __future__ import annotations
import math
import ezdxf
from ezdxf import path as ezpath
from ezdxf.disassemble import recursive_decompose
from .dxfout import placement

_SKIP = {'TEXT', 'MTEXT', 'ATTDEF', 'ATTRIB', 'HATCH', 'SOLID', 'POINT', 'WIPEOUT', 'IMAGE', 'DIMENSION'}

def _placeholder_doc():
    doc = ezdxf.new()
    from .dxfout import _placeholders
    _placeholders(doc)
    return doc

class BlockShapes:
    """Caches each block as polylines in block units."""
    def __init__(self, library_doc):
        self.doc = library_doc
        self.fallback = _placeholder_doc()
        self.cache = {}

    def block(self, name):
        if self.doc is not None and name and self.doc.blocks.get(name) is not None:
            return self.doc.blocks.get(name)
        return self.fallback.blocks.get(name if name in ('SMOKE', 'SMOKE_CO') else 'UNMAPPED')

    def polys(self, name):
        if name in self.cache: return self.cache[name]
        out = []
        blk = self.block(name)
        try:
            for e in recursive_decompose(blk):
                if e.dxftype() in _SKIP: continue
                try:
                    p = ezpath.make_path(e)
                except Exception:
                    continue
                for sub in p.sub_paths():
                    pts = [(v.x, v.y) for v in sub.flattening(0.25)]
                    if len(pts) >= 2: out.append(pts)
        except Exception:
            pass
        self.cache[name] = out
        return out

    def placed(self, s, name):
        """Polylines of a symbol's block in drawing units, placed and rotated like the saved file."""
        blk = self.block(name)
        x, y, rot = placement(s, blk)
        c, si = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return [[(x + px * c - py * si, y + px * si + py * c) for px, py in pts] for pts in self.polys(name)]
