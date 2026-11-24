"""Ceiling heights from another sheet (the floor plan shows them, the electrical plan often doesn't).

Read the room names and the height notes on the other sheet, line that sheet up with the cleaned plan by
the room names they share, and bring each height across to the same spot."""
from __future__ import annotations
import difflib, math, re
from . import ocr
from .vec import page_text, is_filled

CLG = r"(C[L1I][GC6]\.?|CEILING|CEIL\.?)"
HEIGHT = re.compile(r"\d+' ?-? ?\d*( \d/\d+)?\"? ?" + CLG + r"|\b\d{2,3} ?" + CLG +
                    r"|\bVAULT(ED)?\b|\bTRAY\b|\bCOFFER(ED)?\b")

def is_height(n):
    return bool(HEIGHT.search(n))

def read_sheet(page, progress=lambda m: None):
    """Words on a whole sheet: typed text when it has some, otherwise read from the drawn letters."""
    words = ocr.typed_words(page_text(page), page.rotation)
    if len(words) < 20:
        progress('Reading the sheet (a minute or so)')
        letters = [p for p in page.get_drawings() if not is_filled(p) and max(p['rect'].width, p['rect'].height) < 30]
        r = page.rect if page.rotation % 180 == 0 else page.mediabox
        words += ocr.read_words(letters, (r.x0, r.y0, r.x1, r.y1), page.rotation)
    return words

def _room_key(n, names):
    n = re.sub(r'^OPT\.? +', '', n)
    m = difflib.get_close_matches(n, list(names), n=1, cutoff=0.8)
    return names[m[0]] if m else None

def estimate(words, plan_rooms, room_names, tol=45.0):
    """Rough offset (other sheet -> this plan's page) from the room names both sheets share.
    Room names sit in slightly different spots on each sheet, so it's only good to a few dozen points."""
    key = lambda n: re.sub(r'^OPT\.? +', '', n)
    other = [(_room_key(HEIGHT.sub('', w['n']).strip(' /'), room_names), w['cx'], w['cy']) for w in words]
    deltas = [(px - ox, py - oy) for name, px, py in plan_rooms for oname, ox, oy in other if oname and oname == key(name)]
    best = []
    for d in deltas:
        near = [e for e in deltas if abs(e[0] - d[0]) < tol and abs(e[1] - d[1]) < tol]
        if len(near) > len(best): best = near
    if len(best) < 2 and len(plan_rooms) > 1: return None              # one match alone could be the wrong floor
    if not best: return None
    xs = sorted(e[0] for e in best); ys = sorted(e[1] for e in best)
    return xs[len(xs) // 2], ys[len(ys) // 2], len(best)

def refine(plan_walls, region, other_paths, est, pens, reach=80.0):
    """Line the walls up exactly: the plan's walls slid over the other sheet's, near the rough offset."""
    import cv2, numpy as np
    from .vec import raster, pen_key
    dx, dy = est[0], est[1]
    oreg = (region[0] - dx - reach, region[1] - dy - reach, region[2] - dx + reach, region[3] - dy + reach)
    O = [p for p in other_paths if pen_key(p) in pens and p['rect'].x1 >= oreg[0] and p['rect'].x0 <= oreg[2]
         and p['rect'].y1 >= oreg[1] and p['rect'].y0 <= oreg[3]]
    if len(plan_walls) < 10 or len(O) < 10: return est
    e = cv2.GaussianBlur(raster(plan_walls, region, 1.0).astype(np.float32), (0, 0), 2)
    o = cv2.GaussianBlur(raster(O, oreg, 1.0).astype(np.float32), (0, 0), 2)
    if o.shape[0] < e.shape[0] or o.shape[1] < e.shape[1]: return est
    r = cv2.matchTemplate(o, e, cv2.TM_CCOEFF_NORMED); _, score, _, loc = cv2.minMaxLoc(r)
    if score < 0.2: return est
    return region[0] - (oreg[0] + loc[0]), region[1] - (oreg[1] + loc[1]), est[2]

def transfer(words, plan_rooms, room_names, offset):
    """Bring the other sheet's ceiling heights across. Each height goes with the room name just above it on
    that sheet, and then under the same room's name on this plan; a height with no room name (a toilet room)
    lands where it was. Returns [(text, x, y, plan_room_or_None)] in this plan's page points."""
    dx, dy = offset[0], offset[1]
    names = [(_room_key(HEIGHT.sub('', w['n']).strip(' /'), room_names), w) for w in words]
    names = [(k, w) for k, w in names if k]
    out = []
    for w in words:
        m = HEIGHT.search(w['n'])
        if not m: continue
        rest = HEIGHT.sub('', w['n']).strip(' /')
        # 'KITCHEN 9'-0 CLG.' on one line: just the height; 'BOX UP TO 8'-9 1/8 CLG.': all of it
        text = (w['n'][m.start():] if rest and _room_key(rest, room_names) else w['n']).strip()
        text = re.sub(r"(\d+' ?-? ?\d*( \d/\d+)?)\"? ?C[L1I][GC6]\.?", r'\1" CLG.', text)   # the inch mark the reading drops
        x, y = w['cx'] + dx, w['cy'] + dy
        own = [(k, v) for k, v in names if v is w or (abs(v['cx'] - w['cx']) < 40 and -8 <= w['cy'] - v['cy'] < 30)]
        room = None
        if own:
            k, v = min(own, key=lambda kv: abs(kv[1]['cy'] - w['cy']))
            same = [r for r in plan_rooms if re.sub(r'^OPT\.? +', '', r[0]) == k]
            if same:
                r = min(same, key=lambda r: math.hypot(r[1] - x, r[2] - y))
                if math.hypot(r[1] - x, r[2] - y) < 90: room = r
        out.append((text, x, y, room))
    return out
