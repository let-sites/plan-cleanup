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
        self.cache = {}; self.cache_text = {}

    def block(self, name):
        if self.doc is not None and name and self.doc.blocks.get(name) is not None:
            return self.doc.blocks.get(name)
        return self.fallback.blocks.get(name if name in ('SMOKE', 'SMOKE_CO') else 'UNMAPPED')

    def texts(self, name):
        """(x, y, height, rotation, text) of single-line texts in the block, block units."""
        self.polys(name)
        return self.cache_text.get(name, [])

    def polys(self, name):
        if name in self.cache: return self.cache[name]
        out = []; txt = []
        blk = self.block(name)
        try:
            for e in recursive_decompose(blk):
                if e.dxftype() in ('TEXT', 'ATTDEF') and e.dxf.get('text', ''):
                    ins = e.dxf.insert
                    txt.append((ins.x, ins.y, float(e.dxf.get('height', 1.0)), float(e.dxf.get('rotation', 0.0)), e.dxf.text))
                    continue
                if e.dxftype() == 'MTEXT':
                    t = e.plain_text().split('\n')[0].strip()
                    if t:
                        h = float(e.dxf.get('char_height', 1.0)); ap = int(e.dxf.get('attachment_point', 1))
                        ins = e.dxf.insert; w = 0.75 * h * len(t)
                        x = ins.x - (w / 2 if ap in (2, 5, 8) else w if ap in (3, 6, 9) else 0)
                        y = ins.y - (h if ap <= 3 else h / 2 if ap <= 6 else 0)      # to the baseline
                        txt.append((x, y, h, float(e.dxf.get('rotation', 0.0)), t))
                    continue
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
        self.cache[name] = out; self.cache_text[name] = txt
        return out

    def placed_texts(self, s, name):
        blk = self.block(name)
        x, y, rot = placement(s, blk)
        c, si = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return [(x + px * c - py * si, y + px * si + py * c, h, rot + r, t) for px, py, h, r, t in self.texts(name)]

    def placed(self, s, name):
        """Polylines of a symbol's block in drawing units, placed and rotated like the saved file."""
        blk = self.block(name)
        x, y, rot = placement(s, blk)
        c, si = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return [[(x + px * c - py * si, y + px * si + py * c) for px, py in pts] for pts in self.polys(name)]
