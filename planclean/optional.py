"""Optional items a builder draws on their own layers: when those layers are left off, their notes go too."""
from __future__ import annotations
import math, re
from .vec import page_text, is_filled

OPT = re.compile(r'^\s*OPT(ION(AL)?)?\b\.?', re.I)


def _near_rect(pt, r, d):
    return (max(r[0] - pt[0], 0, pt[0] - r[2]) ** 2 + max(r[1] - pt[1], 0, pt[1] - r[3]) ** 2) <= d * d


def notes_on_dropped(page, region, kept, dropped):
    """An 'OPT. ...' note whose leaders all point at items left off (Pulte's 'OPT. PENDANT LIGHTS' with three
    arrows at the pendants): the note, its leaders and their arrowheads go too.
    Returns (ids of kept paths to leave off, boxes of the notes' text to leave off)."""
    if not dropped: return set(), []
    drects = [tuple(p['rect']) for p in dropped]
    lines = page_text(page, region)
    gone, boxes = set(), []
    for l in lines:
        if not OPT.match(l['text']): continue
        # the note: this line and the lines stacked under it
        b = list(l['bbox']); h = max(b[3] - b[1], 1.0); grew = True; used = {id(l)}
        while grew:
            grew = False
            for m in lines:
                if id(m) in used: continue
                mb = m['bbox']
                if abs((mb[3] - mb[1]) - h) < 0.4 * h and 0 <= mb[1] - b[3] < 0.8 * h and min(mb[2], b[2]) - max(mb[0], b[0]) > 0:
                    b = [min(b[0], mb[0]), b[1], max(b[2], mb[2]), mb[3]]; used.add(id(m)); grew = True
        # its leaders: short straight strokes starting at the note
        leaders = []
        for p in kept:
            if is_filled(p) or len(p['items']) > 3: continue
            it = p['items']
            if any(i[0] != 'l' for i in it): continue
            a, z = (it[0][1].x, it[0][1].y), (it[-1][-1].x, it[-1][-1].y)     # a leader can be two strokes (a dogleg)
            if _near_rect(a, b, 6): leaders.append((p, z))
            elif _near_rect(z, b, 6): leaders.append((p, a))
        far = [(p, e) for p, e in leaders if not _near_rect(e, b, 12)]
        if not far: continue
        if not all(any(_near_rect(e, r, 10) for r in drects) for _, e in far): continue
        for p, e in far:
            gone.add(id(p))
            for q in kept:                       # the arrowhead at the far end
                r = q['rect']
                if is_filled(q) and max(r.width, r.height) < 12 and _near_rect(e, tuple(r), 3): gone.add(id(q))
        boxes.append((b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1))
    return gone, boxes
