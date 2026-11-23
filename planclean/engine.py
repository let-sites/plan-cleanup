"""Clean one PDF sheet: keep the base plan, find symbols, read text, decide what goes."""
from __future__ import annotations
import difflib, math, re
import numpy as np
from . import wiring, detect, ocr
from .vec import sample_lines_dir, polylines, pen_key, in_region, sample_lines, is_filled
from .profile import Profile, NOTE_WORDS
from .result import Result, Sym, Text
from .symbols import ORIENTED

def page_paths(page):
    return page.get_drawings()

def pens_in(paths):
    """Distinct pens with counts and total drawn length (for the pen-role setup screen)."""
    stats = {}
    for p in paths:
        k = pen_key(p); s = stats.setdefault(k, dict(count=0, length=0.0))
        s['count'] += 1
        for q in polylines(p):
            s['length'] += float(np.sum(np.linalg.norm(np.diff(q, axis=0), axis=1))) if len(q) > 1 else 0
    return stats

def guess_roles(paths, electrical_pens=()):
    """First guess at pen roles: heaviest black pen = walls, symbol pens = electrical."""
    st = pens_in(paths)
    roles = {}
    widths = {k: float(k.split()[0]) for k in st if not k.startswith('fill')}
    heavy = max(widths, key=lambda k: (widths[k], st[k]['length'])) if widths else None
    for k in st:
        if k.startswith('fill'): roles[k] = 'wall'
        elif k == heavy: roles[k] = 'wall'
        elif k in electrical_pens: roles[k] = 'electrical'
        else: roles[k] = 'misc'
    return roles

def clean_pdf(page, region, profile: Profile, exclude=(), progress=lambda msg: None) -> Result:
    K = profile.inches_per_pt
    x0, y0 = region[0], region[1]
    def tx(x, y): return ((x - x0) * K, -(y - y0) * K)
    res = Result(); res.to_page = lambda X, Y: (X / K + x0, -Y / K + y0)
    progress('Reading the sheet')
    allp = page_paths(page)
    pl = [p for p in allp if in_region(p, region)]
    for ex in exclude:
        pl = [p for p in pl if not in_region(p, ex)]
    roles = dict(profile.pen_roles)
    role = lambda p: roles.get(pen_key(p), 'misc')
    elec = [p for p in pl if role(p) == 'electrical']
    symbolish = [p for p in pl if role(p) in ('electrical', 'misc', 'opening')]

    # 1. symbols
    progress('Finding symbols')
    img = detect.symbol_image(symbolish, region)
    anc = detect.anchors([p for p in pl if role(p) in ('electrical', 'misc', 'opening')])
    hits = detect.classify(img, region, profile.symbols, anc)

    # 2. text
    progress('Reading text')
    words = ocr.read_words(elec, region, page.rotation)

    # 3. walls (for turning symbols and telling wall lights from ceiling lights)
    walls = sample_lines([p for p in pl if role(p) == 'wall' and not is_filled(p)])
    faces = sample_lines([p for p in pl if role(p) in ('wall', 'opening', 'casework') and not is_filled(p)
                          and all(i[0] in ('l', 're', 'qu') for i in p['items'])])
    anyline, anydir = sample_lines_dir([p for p in pl if role(p) not in ('electrical', 'remove')
                                        and all(i[0] in ('l', 're', 'qu') for i in p['items'])])
    RAYS = ((1, 0), (-1, 0), (0, 1), (0, -1))
    def wall_along(x, y, dx_, dy_, reach=18.0):
        """Distance to the nearest line straight out from (x, y) that crosses the ray square-on.
        Lines closer than 3 or running along the ray are the symbol's own tails, not a wall."""
        if not len(anyline): return None
        rel = anyline - (x, y)
        along = rel[:, 0] * dx_ + rel[:, 1] * dy_; perp = np.abs(rel[:, 0] * dy_ - rel[:, 1] * dx_)
        square = np.abs(anydir[:, 0] * dx_ + anydir[:, 1] * dy_) < 0.35
        m = (along > 3.0) & (along < reach) & (perp < 1.5) & square
        return float(along[m].min()) if m.any() else None
    def facing(x, y, reach=18.0, hint=()):
        """Distance to the wall a symbol sits on and the unit direction pointing away from it (page coords).
        Walls are found straight up, down, left or right, so symbols always sit square to them.
        hint: ends of the builder's own stem/tails; a wall right at the end of one wins."""
        # hint is a flat list of stroke end pairs; the longest straight stroke (the stem) speaks first
        segs = sorted(((hint[i], hint[i + 1]) for i in range(0, len(hint) - 1, 2)),
                      key=lambda ab: -math.hypot(ab[1][0] - ab[0][0], ab[1][1] - ab[0][1]))
        for (ax_, ay_), (bx_, by_) in segs:
            sx, sy = bx_ - ax_, by_ - ay_
            if math.hypot(sx, sy) < 6 or min(abs(sx), abs(sy)) * 3 > max(abs(sx), abs(sy)): continue   # short or slanted
            for ex, ey in ((ax_, ay_), (bx_, by_)):
                vx, vy = ex - x, ey - y
                # look along the stem, toward this end of it
                dx_, dy_ = (math.copysign(1, vx or 1), 0) if abs(sx) >= abs(sy) else (0, math.copysign(1, vy or 1))
                dd = wall_along(x, y, dx_, dy_, reach)
                if dd is not None and abs(dd - abs(vx * dx_ + vy * dy_)) < 3.0: return dd, (-dx_, -dy_)
        best = None
        for dx_, dy_ in RAYS:
            dd = wall_along(x, y, dx_, dy_, reach)
            if dd is not None and (best is None or dd < best[0]): best = (dd, (-dx_, -dy_))
        if best: return best
        d, wp = nearest(faces, x, y)
        ux, uy = x - wp[0], y - wp[1]
        ux, uy = (math.copysign(1, ux), 0) if abs(ux) >= abs(uy) else (0, math.copysign(1, uy))
        return d, (ux, uy)
    def nearest(pts, x, y):
        if len(pts) == 0: return 1e9, (x, y + 1)
        d = np.hypot(pts[:, 0] - x, pts[:, 1] - y); i = int(d.argmin()); return float(d[i]), pts[i]

    used = set()
    for h in hits:
        h['mods'] = []
        for w in words:
            if math.hypot(w['cx'] - h['x'], w['cy'] - h['y']) < 16:
                for m in ('GFI', 'WP', 'DISK', '220', '240', 'CLG'):
                    if m in w['n'].replace(' ', ''):
                        h['mods'].append(m); used.add(id(w))
        if h['cls'] in ('ceiling_light', 'wall_light'):
            d, _ = nearest(walls, h['x'], h['y'])
            h['cls'] = 'wall_light' if d < profile.wall_light_max_dist else 'ceiling_light'
        if h['cls'] == 'ceiling_light' and 'DISK' in h['mods']:
            h['cls'] = 'disk_light'
        # A wall symbol labelled 220V is a 220/240V outlet (its three lines can read as a wall light).
        if h['cls'] in ('outlet', 'wall_light', 'ceiling_light') and ('220' in h['mods'] or '240' in h['mods']):
            h['cls'] = 'outlet_240'
    # "BLK & WIRE FOR CLG. FAN" -> the nearest ceiling light becomes a fan
    for w in words:
        if 'FAN' in w['n'] and ('CLG' in w['n'] or 'WIRE' in w['n']):
            group = [w] + [v for v in words if v is not w and 'WIRE' in v['n'] and math.hypot(v['cx'] - w['cx'], v['cy'] - w['cy']) < 20]
            cands = [h for h in hits if h['cls'] in ('ceiling_light', 'disk_light')]
            if cands:
                h = min(cands, key=lambda h: math.hypot(h['x'] - w['cx'], h['y'] - w['cy']))
                if math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < 60:
                    h['cls'] = 'fan'; h['mods'].append('note: ' + ' '.join(g['text'] for g in group))
                    h['review'] = 'Turned into a fan from the builder note'
                    for g in group: h.setdefault('note_boxes', []).append(g['bb'])   # wires drawn to the note go to the fan
                    for g in group: used.add(id(g))

    # 4. words -> room names, kept notes, dropped labels
    drops = profile.compiled_drop()
    rooms, notes = [], []
    for w in words:
        if id(w) in used: continue
        n = w['n']
        if re.fullmatch(r"[&$S ]+", n) or any(p.match(n.replace(' ', '')) or p.match(n) for p in drops):
            res.dropped_labels.append(n); continue
        m = difflib.get_close_matches(n, list(profile.room_names), n=1, cutoff=0.6)
        if m and w['size'] >= profile.room_text_min_size:
            w['room'] = profile.room_names[m[0]]; rooms.append(w)
        else:
            # short labels next to a symbol (WP, GFI, GF, 220, Q, 3(T)...) describe the builder's symbol: drop them
            short = len(re.sub(r'[^A-Za-z0-9]', '', n)) <= 5
            if short and any(math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < 26 for h in hits):
                res.dropped_labels.append(n); continue
            notes.append(w)
    notes = ocr.stack_lines(notes)

    # 5. which paths stay
    progress('Cleaning')
    radius = {'switch': 5.5, 'dimmer': 5.5, 'smoke': 9.5, 'co_smoke': 9.5}
    removed = {'symbol parts': 0, 'text strokes': 0, 'loose glyphs': 0, 'removed pens': 0}
    # Containment tests done in bulk with numpy: a plan can have tens of thousands of
    # paths and thousands of symbol hits or words, and a Python double loop over them
    # takes minutes.
    R = np.array([[p['rect'].x0, p['rect'].y0, p['rect'].x1, p['rect'].y1] for p in pl], float).reshape(-1, 4)
    def contained(boxes):
        out = np.zeros(len(R), bool)
        if len(boxes) == 0 or len(R) == 0: return out
        B = np.asarray(boxes, float)
        for i in range(0, len(R), 2000):
            r = R[i:i + 2000, None, :]
            out[i:i + 2000] = ((r[..., 0] >= B[:, 0]) & (r[..., 2] <= B[:, 2]) &
                               (r[..., 1] >= B[:, 1]) & (r[..., 3] <= B[:, 3])).any(axis=1)
        return out
    hit_boxes = [(h['x'] - radius.get(h['cls'], 7.5), h['y'] - radius.get(h['cls'], 7.5),
                  h['x'] + radius.get(h['cls'], 7.5), h['y'] + radius.get(h['cls'], 7.5)) for h in hits]
    room_ids = {id(w) for w in rooms}
    word_boxes = []
    for w in words:
        pad = 4.0 if id(w) in room_ids else 0.8; b = w['bb']
        word_boxes.append((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
    in_hit, in_word = contained(hit_boxes), contained(word_boxes)
    hxy = np.array([[h['x'], h['y']] for h in hits], float).reshape(-1, 2)
    def near_hit(pt, r):
        if len(hxy) == 0: return -1
        d = np.hypot(hxy[:, 0] - pt[0], hxy[:, 1] - pt[1]); i = int(d.argmin())
        return i if d[i] <= r else -1
    STEM_R = 15.0
    stems = []                       # (end point the wiring may attach to, hit index)
    hint_ends = {}                   # hit index -> ends of its stem / tail strokes (they point at the wall)
    def trim(piece):
        """Cut out the segments of a wire that belong to a symbol (stem, tails, crosshair)."""
        if len(hxy) == 0: return [piece]
        d = np.hypot(piece[:, None, 0] - hxy[None, :, 0], piece[:, None, 1] - hxy[None, :, 1])
        d[:, [i for i, h in enumerate(hits) if h['cls'] in ('smoke', 'co_smoke')]] = 1e9   # wires pass by detectors untouched
        idx = d.argmin(axis=1); near = d[np.arange(len(piece)), idx] <= STEM_R
        seg = np.hypot(*np.diff(piece, axis=0).T)
        # both ends at the same symbol; the bits of a curved wire that swing past a symbol are short: keep them
        drop = near[:-1] & near[1:] & (idx[:-1] == idx[1:]) & (seg >= 3.0)
        if not drop.any(): return [piece]
        out, cur = [], [piece[0]]
        for i, dr in enumerate(drop):
            if dr:
                if len(cur) > 1: out.append(np.array(cur)); stems.append((tuple(cur[-1]), int(idx[i])))
                elif i > 0 and not drop[i - 1]: pass
                cur = [piece[i + 1]]
                if i + 1 < len(drop) and not drop[i + 1]: stems.append((tuple(piece[i + 1]), int(idx[i])))
            else:
                cur.append(piece[i + 1])
        if len(cur) > 1: out.append(np.array(cur))
        for i in np.nonzero(drop)[0]:
            hint_ends.setdefault(int(idx[i]), []).extend([tuple(piece[i]), tuple(piece[i + 1])])
        removed['symbol parts'] += int(drop.sum())
        return out
    wire_pieces = []                 # connected polylines of the kept electrical paths
    kept_verbatim = set()
    for k, p in enumerate(pl):
        rl = role(p); r = p['rect']
        if rl == 'remove': removed['removed pens'] += 1; continue
        if in_hit[k] and is_filled(p) and max(r.width, r.height) < 25:
            removed['symbol parts'] += 1; continue           # a filled symbol (smoke detector dot), whatever its pen
        if rl in ('electrical', 'misc', 'opening'):
            # long single strokes are wiring that just touches the symbol: keep them
            if in_hit[k] and not (rl == 'electrical' and len(p['items']) == 1 and max(r.width, r.height) > 7):
                removed['symbol parts'] += 1; continue
            # a short straight stroke with both ends at one symbol is part of it
            # (a switch's stem, a light's crosshair, an outlet's tails)
            if len(p['items']) == 1 and p['items'][0][0] == 'l' and len(hxy):
                a, b = p['items'][0][1], p['items'][0][2]
                ia, ib = near_hit((a.x, a.y), STEM_R), near_hit((b.x, b.y), STEM_R)
                if ia >= 0 and ia == ib:
                    h = hits[ia]
                    far = (a.x, a.y) if math.hypot(a.x - h['x'], a.y - h['y']) >= math.hypot(b.x - h['x'], b.y - h['y']) else (b.x, b.y)
                    hint_ends.setdefault(ia, []).extend([(a.x, a.y), (b.x, b.y)])
                    stems.append((far, ia)); removed['symbol parts'] += 1; continue
        if rl == 'electrical':
            if in_word[k]:
                removed['text strokes'] += 1; continue
            if len(p['items']) >= 2 and max(r.width, r.height) < 9 and not any(i[0] == 'c' for i in p['items']):
                removed['loose glyphs'] += 1; continue
        if rl == 'electrical':
            for q in wiring.pieces_of(polylines(p)): wire_pieces += trim(q)
            continue
        if rl == 'misc' and len(p['items']) == 1 and p['items'][0][0] == 'l' and max(r.width, r.height) < 6 \
                and near_hit(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2), 28) >= 0:
            removed['stray letters'] = removed.get('stray letters', 0) + 1; continue   # a stroke of a label by a symbol
        out_role = rl; kept_verbatim.add(k)
        for q in polylines(p):
            res.lines.append((out_role, [tx(*v) for v in q], is_filled(p) and len(q) > 2))

    # what the builder had that is not in the result as-is (for the 'show what was removed' view)
    res.original = [[tx(*v) for v in q] for k, p in enumerate(pl) if k not in kept_verbatim for q in polylines(p)]

    # 5b. wiring: join the builder's dashes back together and redraw each wire as one clean
    #     arc (or line) that runs from the centre of one symbol to the centre of the other
    removed.setdefault('stray letters', 0)
    ATTACH = 25.0
    def dash_dot(seq):
        """Wires are drawn dash-dot (long and short pieces); cabinet and soffit lines are even dashes."""
        if len(seq) < 3: return False
        L = sorted(float(np.sum(np.hypot(*np.diff(q, axis=0).T))) for q in seq)
        return L[len(L) // 4] < 0.45 * L[-1 - len(L) // 4]
    # long solid strokes (stair rails, boxes) are never dashes of a wire: keep them as drawn
    SOLID = 30.0
    plen = lambda q: float(np.sum(np.hypot(*np.diff(q, axis=0).T)))
    for q in wire_pieces:
        if plen(q) > SOLID: res.lines.append(('wiring', [tx(*v) for v in q], False))
    wire_pieces = [q for q in wire_pieces if plen(q) <= SOLID]
    NO_WIRES = ('smoke', 'co_smoke')                        # smoke / CO detectors never have wires
    wire_ok = np.array([h['cls'] not in NO_WIRES for h in hits], bool)
    def wire_hit(pt):
        """The symbol a wire end belongs to: a symbol within reach, or a fan whose builder note the wire runs to."""
        if len(hxy):
            d = np.hypot(hxy[:, 0] - pt[0], hxy[:, 1] - pt[1]); d[~wire_ok] = 1e9
            i = int(d.argmin())
            if d[i] <= ATTACH: return i
        for i, h in enumerate(hits):
            for b in h.get('note_boxes', ()):
                if b[0] - 12 <= pt[0] <= b[2] + 12 and b[1] - 12 <= pt[1] <= b[3] + 12: return i
        return -1
    def to_note(i, pt):
        return i >= 0 and math.hypot(hits[i]['x'] - pt[0], hits[i]['y'] - pt[1]) > ATTACH
    for seq in wiring.chain(wire_pieces, gap=16.0):
        P = np.vstack([seq[0]] + [q[1:] if np.hypot(*(q[0] - seq[0][-1])) < 0.05 else q for q in seq[1:]])
        length = float(np.sum(np.hypot(*np.diff(P, axis=0).T)))
        ia, ib = wire_hit(P[0]), wire_hit(P[-1])
        if float((P.max(axis=0) - P.min(axis=0)).max()) < 8 and near_hit(P.mean(axis=0), 28) >= 0:
            removed['stray letters'] += 1; continue          # bits of GFI / WP / 220 labels
        if len(seq) == 1:
            res.lines.append(('wiring', [tx(*v) for v in P], False)); continue   # a solid stroke, not a dashed wire: leave it
        # curved or dash-dot runs are wires; straight plain dashes are cabinet or soffit lines: keep them
        C = P[-1] - P[0]; Lc = float(np.hypot(*C))
        bow = float(np.abs((P - P[0]) @ (np.array([-C[1], C[0]]) / Lc)).max()) if Lc > 1 else 0.0
        curved = bow >= max(1.5, 0.04 * Lc)
        closed = Lc < 0.3 * length                          # loops back on itself (range burners, circles)
        if (not curved and not dash_dot(seq)) or (closed and ia < 0 and ib < 0):
            removed['dashed lines kept'] = removed.get('dashed lines kept', 0) + 1
            for q in seq: res.lines.append(('misc' if not curved else 'wiring', [tx(*v) for v in q], False))
            continue
        if ia >= 0 and ia == ib:
            continue                                         # both ends at one symbol: part of it
        def redraw(A, B):
            """One arc (or line) from A to B bowing like the builder's wire, or None if it would not follow it."""
            AB = B - A; L = float(np.hypot(*AB))
            if L < 1: return None
            nrm = np.array([-AB[1], AB[0]]) / L
            off = (P - A) @ nrm; k = int(np.abs(off).argmax())
            f = wiring.arc_through(tuple(A), tuple(P[k]), tuple(B)) if abs(off[k]) > 0.02 * L else ('line', tuple(A), tuple(B))
            curve = wiring.arc_points(*f[1:]) if f[0] == 'arc' else np.linspace(f[1], f[2], 40)
            # every point of the builder's wire must lie close to the new curve
            # (the ends may bend in to the symbol centre, so only the middle of the run is checked)
            n = len(P); mid = P[int(n * 0.15):max(int(n * 0.85), int(n * 0.15) + 1)]
            dev = max(float(np.hypot(curve[:, 0] - x, curve[:, 1] - y).min()) for x, y in mid[::max(1, len(mid) // 40)])
            return f if (dev <= max(5.0, 0.15 * L) or forced) else None
        forced = to_note(ia, P[0]) or to_note(ib, P[-1])
        hA = np.array([hits[ia]['x'], hits[ia]['y']]) if ia >= 0 else None
        hB = np.array([hits[ib]['x'], hits[ib]['y']]) if ib >= 0 else None
        f = None
        for A, B in ((hA, hB), (hA, None), (None, hB), (None, None)):
            f = redraw(A if A is not None else P[0], B if B is not None else P[-1])
            if f: break
        if f is None:                                        # nothing fits: keep the builder's wire, joined up
            res.lines.append(('wiring', [tx(*v) for v in P], False)); continue
        if f[0] == 'arc':
            cx, cy, r, a0, a1 = f[1:]
            # to drawing units (y is flipped, so the arc runs the other way round)
            X, Y = tx(cx, cy); res.arcs.append(('wiring', float(X), float(Y), float(r * K), float((-a1) % 360), float((-a0) % 360)))
        else: res.lines.append(('wiring', [tx(*f[1]), tx(*f[2])], False))

    # 6. symbols in drawing units, facing into the room
    for hi, h in enumerate(hits):
        X, Y = tx(h['x'], h['y'])
        s = Sym(cls=h['cls'], x=X, y=Y, score=round(h['score'], 3), mods=h['mods'], review=h.get('review', ''), src=(h['x'], h['y']))
        d, (ux, uy) = facing(h['x'], h['y'], hint=hint_ends.get(hi, ()))
        s.dx, s.dy = ux, -uy
        if s.cls in ORIENTED and d <= 18: s.wall = d * K      # the block's insert point goes on this wall line
        if not s.review and h['score'] < 0.75: s.review = f"Low match ({h['score']:.2f})"
        if not s.review and s.cls in ORIENTED and d > 18: s.review = 'No wall nearby to face'
        res.symbols.append(s)
    for w in rooms:
        X, Y = tx(w['cx'], w['cy']); res.texts.append(Text('room', [w['room']], X, Y))
    for w in notes:
        X, Y = tx(w['cx'], w['cy'])
        lines = [' '.join(NOTE_WORDS.get(t, t) for t in ln.split()) for ln in w['lines']]
        res.texts.append(Text('note', lines, X, Y, review='Builder note kept: check it' if w['score'] < 0.7 else ''))
    res.stats = dict(removed=removed, words=len(words), symbols=len(hits))
    return res
