"""Clean one PDF sheet: keep the base plan, find symbols, read text, decide what goes."""
from __future__ import annotations
import difflib, math, re
import numpy as np
from . import wiring, detect, ocr
from .vec import polylines, pen_key, in_region, sample_lines, is_filled
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
    def nearest(pts, x, y):
        if len(pts) == 0: return 1e9, (x, y + 1)
        d = np.hypot(pts[:, 0] - x, pts[:, 1] - y); i = int(d.argmin()); return float(d[i]), pts[i]

    used = set()
    for h in hits:
        h['mods'] = []
        for w in words:
            if math.hypot(w['cx'] - h['x'], w['cy'] - h['y']) < 16:
                for m in ('GFI', 'WP', 'DISK', '220', 'CLG'):
                    if m in w['n'].replace(' ', ''):
                        h['mods'].append(m); used.add(id(w))
        if h['cls'] in ('ceiling_light', 'wall_light'):
            d, _ = nearest(walls, h['x'], h['y'])
            h['cls'] = 'wall_light' if d < profile.wall_light_max_dist else 'ceiling_light'
        if h['cls'] == 'ceiling_light' and 'DISK' in h['mods']:
            h['cls'] = 'disk_light'
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
            notes.append(w)
    notes = ocr.stack_lines(notes)

    # 5. which paths stay
    progress('Cleaning')
    radius = {'switch': 5.5, 'dimmer': 5.5}
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
    def trim(piece):
        """Cut out the segments of a wire that belong to a symbol (stem, tails, crosshair)."""
        if len(hxy) == 0: return [piece]
        d = np.hypot(piece[:, None, 0] - hxy[None, :, 0], piece[:, None, 1] - hxy[None, :, 1])
        idx = d.argmin(axis=1); near = d[np.arange(len(piece)), idx] <= STEM_R
        drop = near[:-1] & near[1:] & (idx[:-1] == idx[1:])          # both ends at the same symbol
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
        removed['symbol parts'] += int(drop.sum())
        return out
    wire_pieces = []                 # connected polylines of the kept electrical paths
    for k, p in enumerate(pl):
        rl = role(p); r = p['rect']
        if rl == 'remove': removed['removed pens'] += 1; continue
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
                    stems.append((far, ia)); removed['symbol parts'] += 1; continue
        if rl == 'electrical':
            if in_word[k]:
                removed['text strokes'] += 1; continue
            if len(p['items']) >= 2 and max(r.width, r.height) < 9 and not any(i[0] == 'c' for i in p['items']):
                removed['loose glyphs'] += 1; continue
        if rl == 'electrical':
            for q in wiring.pieces_of(polylines(p)): wire_pieces += trim(q)
            continue
        out_role = rl
        for q in polylines(p):
            res.lines.append((out_role, [tx(*v) for v in q], is_filled(p) and len(q) > 2))

    # 5b. wiring: join the builder's dashes back into solid lines and arcs
    removed['stray letters'] = 0
    stem_pts = np.array([s_[0] for s_ in stems], float).reshape(-1, 2)
    for seq in wiring.chain(wire_pieces):
        P = np.vstack([seq[0]] + [q[1:] if np.hypot(*(q[0] - seq[0][-1])) < 0.05 else q for q in seq[1:]])
        length = sum(float(np.sum(np.hypot(*np.diff(q, axis=0).T))) for q in seq)
        if len(seq) == 1 and length < 8 and near_hit(P.mean(axis=0), 20) >= 0:
            removed['stray letters'] += 1; continue          # bits of GFI / WP / 220 labels
        D = np.array([tx(*v) for v in P])
        f = wiring.fit(D) if len(seq) > 1 else ('poly', D)
        if f[0] == 'arc' and len(seq) > 1:
            res.arcs.append(('wiring',) + tuple(float(v) for v in f[1:]))
        else:   # straight or cornered runs keep the builder's own strokes
            for q in seq: res.lines.append(('wiring', [tx(*v) for v in q], False))
        # a wire that ended at a removed stem now runs into the symbol
        for end in (P[0], P[-1]):
            if len(stem_pts) == 0: break
            d = np.hypot(stem_pts[:, 0] - end[0], stem_pts[:, 1] - end[1]); j = int(d.argmin())
            if d[j] < 1.5:
                h = hits[stems[j][1]]; v = np.array([h['x'], h['y']]) - end; L = float(np.hypot(*v))
                if L > 4: res.lines.append(('wiring', [tx(*end), tx(*(end + v * (L - 3.5) / L))], False))

    res.arcs = wiring.merge_arcs(res.arcs, gap=10.0 * K)
    res.arcs = wiring.extend_to_symbols(res.arcs, np.array([tx(h['x'], h['y']) for h in hits], float).reshape(-1, 2),
                                        reach=25.0 * K, stop=5.0 * K)

    # 6. symbols in drawing units, facing into the room
    for h in hits:
        X, Y = tx(h['x'], h['y'])
        s = Sym(cls=h['cls'], x=X, y=Y, score=round(h['score'], 3), mods=h['mods'], review=h.get('review', ''), src=(h['x'], h['y']))
        d, wp = nearest(faces, h['x'], h['y'])
        ux, uy = h['x'] - wp[0], h['y'] - wp[1]; L = math.hypot(ux, uy) or 1
        s.dx, s.dy = ux / L, -uy / L
        if not s.review and h['score'] < 0.75: s.review = f"Low match ({h['score']:.2f})"
        if not s.review and s.cls in ORIENTED and d > 14: s.review = 'No wall nearby to face'
        res.symbols.append(s)
    for w in rooms:
        X, Y = tx(w['cx'], w['cy']); res.texts.append(Text('room', [w['room']], X, Y))
    for w in notes:
        X, Y = tx(w['cx'], w['cy'])
        lines = [' '.join(NOTE_WORDS.get(t, t) for t in ln.split()) for ln in w['lines']]
        res.texts.append(Text('note', lines, X, Y, review='Builder note kept: check it' if w['score'] < 0.7 else ''))
    res.stats = dict(removed=removed, words=len(words), symbols=len(hits))
    return res
