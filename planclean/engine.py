"""Clean one PDF sheet: keep the base plan, find symbols, read text, decide what goes."""
from __future__ import annotations
import difflib, math, re
import numpy as np
from . import wiring, detect, ocr
from .vec import drop_layers, sample_lines_dir, polylines, pen_key, in_region, sample_lines, is_filled, page_text, text_anchors
from .profile import Profile, NOTE_WORDS
from .result import Result, Sym, Text, turn_sym
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

TITLE_H = 9.0          # inches: the sheet title's text height (Settings.text_height_title)

# a dimension or centre-line mark the builder typed (2'-6", (TYP.), CL): not a note
DIMENSION = re.compile(r"\d+'(-? ?\d+( \d/\d)?)?|\(TYP\.?\)|C/?L")

def paired_walls(paths, K, thick=(3.0, 9.0), least=12.0):
    """ids of paths that are wall faces: straight, square to the sheet, with another such line a wall's thickness
    (3 to 9 inches) away running alongside it for at least a foot. K: inches per point."""
    tmin, tmax, lmin = thick[0] / K, thick[1] / K, least / K
    segs = {0: [], 1: []}               # 0: horizontal (y, x0, x1), 1: vertical (x, y0, y1)
    for pi, p in enumerate(paths):
        for q in polylines(p):
            for a, b in zip(q, q[1:]):
                dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
                if max(dx, dy) < lmin: continue
                if dy <= 0.03 * dx: segs[0].append(((a[1] + b[1]) / 2, min(a[0], b[0]), max(a[0], b[0]), pi))
                elif dx <= 0.03 * dy: segs[1].append(((a[0] + b[0]) / 2, min(a[1], b[1]), max(a[1], b[1]), pi))
    good, total = {}, {}
    for d in (0, 1):
        L = sorted(segs[d])
        for _, a0, a1, pi in L: total[pi] = total.get(pi, 0.0) + (a1 - a0)
        hit = [0.0] * len(L)
        for i in range(len(L)):
            c, a0, a1, _ = L[i]
            for j in range(i + 1, len(L)):
                c2, b0, b1, _ = L[j]
                if c2 - c > tmax: break
                if c2 - c < tmin: continue
                ov = min(a1, b1) - max(a0, b0)
                if ov >= lmin * 0.8:
                    hit[i] = max(hit[i], ov); hit[j] = max(hit[j], ov)
        for k, (_, a0, a1, pi) in enumerate(L):
            if hit[k]: good[pi] = good.get(pi, 0.0) + (a1 - a0)
    return {id(paths[pi]) for pi, g in good.items() if g >= 0.6 * total.get(pi, 1e9)}

def clean_pdf(page, region, profile: Profile, exclude=(), progress=lambda msg: None) -> Result:
    K = profile.inches_per_pt
    x0, y0 = region[0], region[1]
    def tx(x, y): return ((x - x0) * K, -(y - y0) * K)
    res = Result(); res.to_page = lambda X, Y: (X / K + x0, -Y / K + y0)
    progress('Reading the sheet')
    allp = drop_layers(page_paths(page), profile)
    pl = [p for p in allp if in_region(p, region)]
    for ex in exclude:
        pl = [p for p in pl if not in_region(p, ex)]
    roles = dict(profile.pen_roles)
    role = lambda p: roles.get(pen_key(p), 'misc')
    elec = [p for p in pl if role(p) == 'electrical']
    symbolish = detect.symbol_paths(pl, role)

    # 1. symbols
    progress('Finding symbols')
    texts = page_text(page, region, exclude)            # typed text (some builders), not letters drawn as lines
    img = detect.symbol_image(symbolish, region, texts)
    anc = detect.anchors(symbolish) + text_anchors(texts)
    hits = detect.classify(img, region, profile.symbols, anc)

    # 2. text
    progress('Reading text')
    words = ocr.read_words(elec, region, page.rotation)     # page-fixed tiles: the box drawn doesn't change a word
    if texts: words = ocr.merge_same_line(words + ocr.typed_words(texts, page.rotation))
    # Sheet titles ('ELECTRICAL PLAN - OPT. 4TH CAR GARAGE') are often lettered with the wall pen: read big
    # lettering from short open strokes of the other pens too, and turn it into a clean title.
    letters = [p for p in pl if role(p) in ('wall', 'misc', 'casework') and not is_filled(p)
               and max(p['rect'].width, p['rect'].height) < 30]
    VOCAB = ({t for k in profile.room_names for t in k.split()} |
             {'ELECTRICAL', 'ELECTRIC', 'PLAN', 'OPT.', 'OPTIONAL', 'CAR', 'BAY', 'SCALE', 'FLOOR', 'FIRST', 'SECOND', 'LIGHTING',
              'SPACE', 'READY', 'SPRINKLER', 'ABOVE', 'BELOW', 'LIGHT', 'SWITCH', 'FUTURE', 'PRE-WIRE', 'APPLIANCES',
              'FIREPLACE', 'DOOR', 'WIRE', 'OUTLET', 'CEILING', 'FAN', 'GARAGE', 'DISCONNECT'})
    def known(t): return t in VOCAB or re.fullmatch(r'\d+(ST|ND|RD|TH)', t) is not None
    def split_words(tok):
        """'ELECTRICALPLAN-' -> 'ELECTRICAL PLAN -', '4THCARGARAGE' -> '4TH CAR GARAGE' (known words run together)."""
        core = tok.strip('-'); best = {0: []}
        for i in range(1, len(core) + 1):
            for j in range(max(0, i - 12), i):
                if j in best and known(core[j:i]) and (i not in best or len(best[j]) + 1 < len(best[i])): best[i] = best[j] + [core[j:i]]
        if len(core) in best and len(best[len(core)]) > 1: return ' '.join(best[len(core)]) + (' -' if tok.endswith('-') else '')
        return tok
    def fix_word(tok):
        if known(tok) or len(tok) < 4: return tok
        t2 = split_words(tok)
        if t2 != tok: return t2
        if not tok.isalpha(): return tok
        m = difflib.get_close_matches(tok, list(VOCAB), n=1, cutoff=0.8); return m[0] if m else tok   # 'GARAGI' -> GARAGE
    def title_text(n):
        n = re.sub(r'\bA(?=TH)', '4', n)                               # 'ATH' is 4TH
        n = re.sub(r'\bOP[T1I]?\.', 'OPT.', n)                         # 'OP.' is OPT.
        n = re.sub(r'(OPT\.)(?=\w)', r'\1 ', n)
        return ' '.join(fix_word(t) for t in n.split())
    def title_quality(w):
        toks = re.sub(r'[^A-Z0-9. ]', ' ', title_text(w['n'])).split()
        return sum(len(t) for t in toks if known(t)) / max(1, len(w['n'].replace(' ', ''))) + w['score']
    titles = []
    if len(letters) >= 20:
        # big lettering reads best small (and whole: a title can be longer than a reading tile); read it at a few
        # sizes and keep, for each spot, the reading that makes the most sense
        cands = []
        for sc in (1.5, 2.0, 2.5):
            cands += [w for w in ocr.merge_same_line(ocr._read_area(letters, region, page.rotation, sc))
                      if w['size'] >= 12 and len(re.sub(r'[^A-Z0-9]', '', w['n'])) >= 6 and w['score'] >= 0.5]
        for w in sorted(cands, key=title_quality, reverse=True):
            if not any(ocr._overlap(w['bb'], t['bb']) > 0.4 for t in titles):
                titles.append(w)
        titles = [w for w in titles if title_quality(w) >= 1.2]          # mostly real words, read with some confidence
    # a typed sheet title ('1 1ST LEVEL ELECTRICAL PLAN'): exact already
    # Only the big lettering: 'REFER TO STANDARD / ELECTRICAL PLAN FOR / MORE INFORMATION' in a note box is a note.
    # The title's other big lines come with it ('OPT. CHEF'S KITCHEN' over 'ELECTRICAL PLAN').
    fs = lambda w: w.get('fsize') or w['size']
    seeds = [w for w in words if w.get('typed') and re.search(r'\bPLAN\b', w['n']) and re.search(r'ELEC|LIGHTING|POWER|LEVEL|FLOOR', w['n'])]
    if seeds:
        top = max(fs(w) for w in seeds); seeds = [w for w in seeds if fs(w) >= 0.8 * top]
        grew = True
        while grew:
            grew = False
            for w in words:
                if not w.get('typed') or w in seeds or re.fullmatch(r'[\d ]+', w['n']) or w['n'].startswith('SCALE'): continue
                for t in seeds:
                    h = fs(t)
                    if 0.8 * h <= fs(w) <= 1.25 * h and abs(w['cy'] - t['cy']) < 2.2 * h and \
                            min(w['dbb'][2], t['dbb'][2]) - max(w['dbb'][0], t['dbb'][0]) > -2 * h:
                        seeds.append(w); grew = True; break
    for w in seeds:
        words.remove(w)
        if not any(ocr._overlap(w['bb'], t['bb']) > 0.4 for t in titles):
            w = dict(w); w['n'] = re.sub(r'^\d{1,2} +', '', w['n']); titles.append(w)     # less the detail number in front

    # 3. walls (for turning symbols and telling wall lights from ceiling lights)
    # Some builders (Pulte) draw walls with the same pen as the wiring: find them by shape instead,
    # two straight parallel lines a wall's thickness apart. From here on they count as walls.
    straight_ = lambda p: not is_filled(p) and all(i[0] in ('l', 're', 'qu') for i in p['items'])
    have = paired_walls([p for p in pl if role(p) == 'wall' and straight_(p)], K)
    paired = paired_walls([p for p in elec if straight_(p)], K)
    if len(paired) <= 2 * len(have) + 4: paired = set()        # the wall pen has the walls: these are a few wires side by side
    if paired:
        pen_role = role
        # one pen for everything: its wires are the curves; long straight lines and closed outlines on it are the
        # building (exterior faces, fixtures, cabinets, stairs, dimensions), not wiring
        def _plain(p):
            if not straight_(p): return False
            if p.get('closePath') or any(i[0] in ('re', 'qu') for i in p['items']): return True
            r = p['rect']; return max(r.width, r.height) * K >= 18.0
        plain = {id(p) for p in elec if id(p) not in paired and _plain(p)}
        role = lambda p: 'wall' if id(p) in paired else 'misc' if id(p) in plain else pen_role(p)
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
    RATING_PART = re.compile(r'2[24]0|\b\d{2} ?A\b|[GS]FC')        # bits of a circuit rating ('220V 40A GFCI')
    for h in hits:
        h['mods'] = []; h['shape'] = h['cls']          # what the drawing matched, before notes and walls change it
        for w in words:
            if math.hypot(w['cx'] - h['x'], w['cy'] - h['y']) < 16:
                for m in ('GFI', 'WP', 'DISK', '220', '240', 'CLG'):
                    if m in w['n'].replace(' ', ''):
                        h['mods'].append(m)
                        if not RATING_PART.search(w['n']): used.add(id(w))   # a circuit rating stays as a note
        if h['cls'] in ('ceiling_light', 'wall_light'):
            d, _ = nearest(walls, h['x'], h['y'])
            h['cls'] = 'wall_light' if d < profile.wall_light_max_dist else 'ceiling_light'
        if h['cls'] == 'ceiling_light' and 'DISK' in h['mods']:
            h['cls'] = 'disk_light'
        # A wall symbol labelled 220V is a 220/240V outlet (its three lines can read as a wall light).
        if h['cls'] in ('outlet', 'wall_light', 'ceiling_light') and ('220' in h['mods'] or '240' in h['mods']):
            h['cls'] = 'outlet_240'
    # "EV READY", "EV OUTLET", "CAR CHARGER", "220V" near a symbol: it is a 220/240V outlet (car charger circuit).
    for w in words:
        t = re.sub(r'[^A-Z0-9 ]', ' ', w['text'].upper())
        ev = re.search(r'\bEV(\b|READY|OUTLET|CHARG)|CHARG', t); volts = re.search(r'\b2[24]0', t)
        if ev or volts:
            cands = [h for h in hits if h['cls'] in ('outlet', 'wall_light', 'ceiling_light', 'outlet_240') and 'GFI' not in h['mods']]
            if cands:
                h = min(cands, key=lambda h: math.hypot(h['x'] - w['cx'], h['y'] - w['cy']))
                if math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < (45 if ev else 30):
                    h['cls'] = 'outlet_240'
                    # the words stay on the plan as a note ('EV READY', '220V 40A GFCI')
    # "BLK & WIRE FOR CLG. FAN" -> the nearest ceiling light becomes a fan
    for w in words:
        if 'FAN' in w['n'] and ('CLG' in w['n'] or 'WIRE' in w['n']):
            def gap(a, b):          # space between two word boxes (0 when they touch or overlap)
                return math.hypot(max(0.0, a[0] - b[2], b[0] - a[2]), max(0.0, a[1] - b[3], b[1] - a[3]))
            # the rest of the note ("BLK & WIRE"), however the text reader split it into words
            NOTE_WORD = re.compile(r'BLK|BLACK|WIRE|W[I1]R|FOR|CLG|CEIL|FAN|^[&$+]$|^AND$|^T?BLK')
            group = [w]
            grown = True
            while grown:            # every line of the note, however the text reader split or misread it
                grown = False
                for v in words:
                    if v in group or id(v) in used: continue
                    near = min(gap(v['bb'], g['bb']) for g in group)
                    if near < 12 and (NOTE_WORD.search(v['n']) or len(v['n']) <= 3): group.append(v); grown = True
            cands = [h for h in hits if h['cls'] in ('ceiling_light', 'disk_light', 'fan')]   # a fan already: its note still takes the wires
            if cands:
                h = min(cands, key=lambda h: math.hypot(h['x'] - w['cx'], h['y'] - w['cy']))
                if math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < 60:
                    if h['cls'] != 'fan': h['review'] = 'Turned into a fan from the builder note'
                    h['cls'] = 'fan'; h['mods'].append('note: ' + ' '.join(g['text'] for g in group))
                    for g in group: h.setdefault('note_boxes', []).append(g['bb'])   # wires drawn to the note go to the fan
                    for g in group: used.add(id(g))

    # 4. words -> room names, kept notes, dropped labels
    drops = profile.compiled_drop()
    rooms, notes = [], []
    def rating_text(t):
        """Tidy a circuit rating however it was read: '220V4OAGFCI' -> '220V 40A GFCI'."""
        t = re.sub(r'(2[24]0) ?[VY]? ?(?=\d)', r'\1V ', t)            # '220V4 A' -> '220V 4 A'
        t = re.sub(r'\b(\d)[O0]A', r'\g<1>0A ', t)                     # '4OAGFCI' -> '40A GFCI'
        t = re.sub(r'\b(2[24]0)[VY]?\b', r'\1V', t)
        t = re.sub(r'\b(\d{2}) ?A ?', r'\1A ', t)
        t = re.sub(r'\b[AS]?[GS]FC[I1L]?\b', 'GFCI', re.sub(r'(\d)A([GS]FC)', r'\1A \2', t))
        t = re.sub(r'\b\d\b', '', t)                                 # a lone digit is a half-read amp rating: leave it out
        return re.sub(r'\s+', ' ', t).strip()
    ratings = {}
    for w in words:
        if id(w) in used: continue
        n = w['n']
        if RATING_PART.search(n):
            # the builder's circuit rating under a 220/240V outlet ('220V 40A GFCI') is kept as one note,
            # however the text reader split or garbled it
            near = [h for h in hits if h['cls'] == 'outlet_240' and math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < 45]
            if near:
                h = min(near, key=lambda h: math.hypot(h['x'] - w['cx'], h['y'] - w['cy']))
                ratings.setdefault(id(h), []).append(w); continue
        if re.fullmatch(r"[&$S\d ]+", n) or DIMENSION.fullmatch(n) or any(p.match(n.replace(' ', '')) or p.match(n) for p in drops):
            res.dropped_labels.append(n); continue
        opt = re.match(r'OPT\.? +', n); rn = n[opt.end():] if opt else n      # 'OPT. DEN': an optional room
        m = difflib.get_close_matches(rn, list(profile.room_names), n=1, cutoff=0.7)
        if not m and len(rn) >= 4:       # a misread ending ('TANG' for TANDEM) is fine when the start is right
            m = [k for k in difflib.get_close_matches(rn, list(profile.room_names), n=1, cutoff=0.6) if k[:3] == rn[:3]]
        if m and w['size'] >= profile.room_text_min_size - 0.5:     # the text reader's box height wobbles a little
            w['room'] = ('OPT. ' if opt else '') + profile.room_names[m[0]]; rooms.append(w)
        else:
            # short labels next to a symbol (WP, GFI, GF, 220, Q, 3(T)...) describe the builder's symbol: drop them
            short = len(re.sub(r'[^A-Za-z0-9]', '', n)) <= 5
            if short and any(math.hypot(h['x'] - w['cx'], h['y'] - w['cy']) < 26 for h in hits):
                res.dropped_labels.append(n); continue
            if len(re.sub(r'[^A-Za-z0-9]', '', n)) <= 1:
                res.dropped_labels.append(n); continue       # a lone letter is a stray mark, not a note
            notes.append(w)
    for group in ratings.values():
        group.sort(key=lambda w: (round(w['dbb'][1] / 6), w['dbb'][0]) if not w['vert'] else (round(w['dbb'][0] / 6), -w['dbb'][3]))
        t = rating_text(' '.join(w['n'] for w in group))
        if not re.search(r'2[24]0', t): continue                    # amps alone don't make a rating
        w = dict(group[0]); w['n'] = w['text'] = re.sub(r'\s+', ' ', t).strip(); w.pop('lines', None)
        for v in group[1:]: ocr._absorb(w, v)
        notes.append(w)
    notes = ocr.stack_lines(notes)

    # builder settings: symbol kinds LET leaves off this builder's plans (their wires go too)
    OUTSIDE_ROOMS = ('GARAGE', 'TANDEM', 'PATIO', 'PORCH', 'DECK')
    def outside_or_garage(h):
        if 'WP' in h['mods']: return True
        if not rooms: return False
        r = min(rooms, key=lambda w: math.hypot(w['cx'] - h['x'], w['cy'] - h['y']))
        return any(k in r['room'] for k in OUTSIDE_ROOMS)
    hidden = set()
    for i, h in enumerate(hits):
        c = h['cls']
        if c in ('outlet', 'ceiling_outlet', 'floor_outlet'):
            if profile.outlets == 'none' or (profile.outlets == 'outside' and not outside_or_garage(h)): hidden.add(i)
        elif c in ('smoke', 'co_smoke') and not profile.detectors: hidden.add(i)
        elif c == 'exhaust_fan' and not profile.exhaust_fans: hidden.add(i)

    # 5. which paths stay
    progress('Cleaning')
    radius = {'switch': 5.5, 'dimmer': 5.5, 'smoke': 9.5, 'co_smoke': 9.5}
    removed = {'symbol parts': 0, 'text strokes': 0, 'loose glyphs': 0, 'removed pens': 0,
               'symbols left off (builder settings)': len(hidden)}
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
    word_boxes, word_boxes_ext = [], []
    for w in words:
        pad = 4.0 if id(w) in room_ids else 0.8; b = w['bb']
        ex = ey = 0.0
        nch = len(re.sub(r'\s', '', w['text']))
        if nch >= 3:     # reach two letters past each end along the line of text: the reader can drop an end letter
            bw, bh = b[2] - b[0], b[3] - b[1]
            if bw >= bh: ex = 2 * bw / nch
            else: ey = 2 * bh / nch
        if nch <= 3 and w['score'] < 0.7 and id(w) not in room_ids:
            b, pad, ex, ey = (1e9, 1e9, 1e9, 1e9), 0.0, 0.0, 0.0   # a doubtful 2-3 letter read (switch '$$' as 'DS') erases nothing
        word_boxes.append((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
        word_boxes_ext.append((b[0] - pad - ex, b[1] - pad - ey, b[2] + pad + ex, b[3] + pad + ey))
    in_hit, in_word, in_word_ext = contained(hit_boxes), contained(word_boxes), contained(word_boxes_ext)
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
    def full_circle(p):
        """A closed path made only of curves: a burner or other round drawing, never a wire."""
        it = p['items']
        if len(it) < 2 or any(i[0] != 'c' for i in it): return False
        a, b = it[0][1], it[-1][-1]
        return math.hypot(a.x - b.x, a.y - b.y) < 0.2
    def smooth_dash(p):
        """A short dash of a curved wire: one open run of segments, end to end, bending gently.
        (Letter strokes left outside word boxes turn sharply or branch.)"""
        it = p['items']
        if any(i[0] != 'l' for i in it): return False
        pts = [it[0][1]]
        for i in it:
            if math.hypot(i[1].x - pts[-1].x, i[1].y - pts[-1].y) > 0.05: return False
            pts.append(i[2])
        if math.hypot(pts[0].x - pts[-1].x, pts[0].y - pts[-1].y) < 2.0: return False
        turn = 0.0
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            u = math.atan2(b.y - a.y, b.x - a.x); v = math.atan2(c.y - b.y, c.x - b.x)
            t = abs((v - u + math.pi) % (2 * math.pi) - math.pi)
            if t > math.radians(30): return False
            turn += t
        return turn < math.radians(50)
    wire_pieces = []                 # connected polylines of the kept electrical paths
    kept_verbatim = set()
    def title_box(t):          # a letter's width past each end: a trailing dash, a dropped end letter
        b = t['bb']; e = 1.2 * t['size']
        return (b[0] - 2 - e, b[1] - 2, b[2] + 2 + e, b[3] + 2) if b[2] - b[0] >= b[3] - b[1] else (b[0] - 2, b[1] - 2 - e, b[2] + 2, b[3] + 2 + e)
    in_title = contained([title_box(t) for t in titles])
    def underline(r):
        """A long thin bar running along a title, just beside it."""
        for t in titles:
            b = t['bb']; tw, th = b[2] - b[0], b[3] - b[1]
            if tw >= th and r.height < 5 and r.width > 0.5 * tw and r.x0 < b[2] and r.x1 > b[0] and -14 < r.y0 - b[3] < 14 + th: return True
            if th > tw and r.width < 5 and r.height > 0.5 * th and r.y0 < b[3] and r.y1 > b[1] and -14 - tw < r.x0 - b[0] < 14 + tw: return True
        return False
    for k, p in enumerate(pl):
        rl = role(p); r = p['rect']
        if rl == 'remove': removed['removed pens'] += 1; continue
        if titles and rl != 'electrical' and (in_title[k] or underline(r)):
            removed['text strokes'] += 1; continue           # the builder's sheet title: written as clean text
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
            # just past a word's ends only letter shapes go (a dropped end letter), never a wire's dashes
            if in_word[k] or (in_word_ext[k] and len(p['items']) >= 2 and not smooth_dash(p) and not full_circle(p)):
                removed['text strokes'] += 1; continue
            if len(p['items']) >= 2 and max(r.width, r.height) < 9 and not any(i[0] == 'c' for i in p['items']) \
                    and not smooth_dash(p):
                removed['loose glyphs'] += 1; continue
        if rl == 'electrical' and not full_circle(p):     # a circle that isn't a symbol (burner) is drawn as is
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
    def straight(q):
        C = q[-1] - q[0]; Lc = float(np.hypot(*C))
        if Lc < 1: return False
        return float(np.abs((q - q[0]) @ (np.array([-C[1], C[0]]) / Lc)).max()) < max(1.0, 0.03 * Lc)
    # (long curved dashes are still wire: some builders draw wires with long dashes)
    solid = [plen(q) > SOLID and straight(q) for q in wire_pieces]
    for q, sd in zip(wire_pieces, solid):
        if sd: res.lines.append(('wiring', [tx(*v) for v in q], False))
    wire_pieces = [q for q, sd in zip(wire_pieces, solid) if not sd]
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
                if b[0] - 16 <= pt[0] <= b[2] + 16 and b[1] - 16 <= pt[1] <= b[3] + 16: return i
        return -1
    def ahead_hit(pt, d, reach=45.0):
        """A loose wire end that points straight at a symbol a little way off ends at that symbol."""
        n = float(np.hypot(*d))
        if n < 1e-6 or not len(hxy): return -1
        d = d / n; best, bd = -1, reach
        for i, h in enumerate(hits):
            if not wire_ok[i]: continue
            v = np.array([h['x'] - pt[0], h['y'] - pt[1]]); L = float(np.hypot(*v))
            if L < bd and L > 1e-6 and float(v @ d) / L > 0.9: best, bd = i, L
        return best
    def to_note(i, pt):
        return i >= 0 and math.hypot(hits[i]['x'] - pt[0], hits[i]['y'] - pt[1]) > ATTACH
    def join_free_ends(chains, gap=16.0, max_turn=100.0):
        """Tight curves (a wire looping round to a light) bend more between dashes than chain() allows.
        Join two chains end to end where both ends are loose (at no symbol) and the wire carries on."""
        cos_max = math.cos(math.radians(max_turn))
        def loose(seq, last):
            """An end at no symbol, or at the same symbol the chain's other end starts from (it is just leaving it)."""
            pt = end(seq, last)[0]
            h = wire_hit(pt)
            if h < 0 or h == wire_hit(end(seq, not last)[0]): return True
            return math.hypot(hits[h]['x'] - pt[0], hits[h]['y'] - pt[1]) > 12.0   # near, but not at, a symbol
        def end(seq, last):
            q = seq[-1] if last else seq[0]
            return (q[-1], wiring._dir(q, True)) if last else (q[0], wiring._dir(q, False))
        def bent(seq):
            P = np.vstack(seq); C = P[-1] - P[0]; Lc = float(np.hypot(*C))
            if Lc < 1: return True
            return float(np.abs((P - P[0]) @ (np.array([-C[1], C[0]]) / Lc)).max()) >= max(1.5, 0.04 * Lc)
        def anchored(seq):
            """One end of the chain is at a switch or light (cabinet lines don't start at those)."""
            for last in (False, True):
                h = wire_hit(end(seq, last)[0])
                if h >= 0 and hits[h]['cls'] not in ('outlet', 'outlet_240', 'ceiling_outlet', 'floor_outlet'):
                    return True
            return False
        # what each chain's ends look like, worked out once (and again only for a chain that just grew)
        def info(seq):
            return (bent(seq), [end(seq, False), end(seq, True)], [loose(seq, False), loose(seq, True)])
        infos = [info(c) for c in chains]
        reach = 2.5 * gap
        while True:
            grid = {}
            for i, (_, ends_, lo) in enumerate(infos):
                for li in (0, 1):
                    if lo[li]:
                        pt = ends_[li][0]; grid.setdefault((int(pt[0] // reach), int(pt[1] // reach)), []).append((i, li))
            best = None
            for i, (bi, ends_i, lo_i) in enumerate(infos):
                for li in (0, 1):
                    if not lo_i[li]: continue
                    pa, da = ends_i[li]
                    gx, gy = int(pa[0] // reach), int(pa[1] // reach)
                    for cx in (gx - 1, gx, gx + 1):
                        for cy in (gy - 1, gy, gy + 1):
                            for j, lj in grid.get((cx, cy), ()):
                                if j <= i: continue
                                bj = infos[j][0]; pb, db = infos[j][1][lj]
                                g = float(np.hypot(*(pb - pa)))
                                if g > reach: continue
                                if g > gap:                          # a longer gap only where a word was written over the wire
                                    m = (pa + pb) / 2
                                    if not any(b[0] <= m[0] <= b[2] and b[1] <= m[1] <= b[3] for b in word_boxes): continue
                                # close by, the wire may bend sharply; across a longer gap (where a word
                                # was drawn over the wire) it must carry straight on
                                if g <= gap and not (bi and bj): continue   # sharp bends only between curved runs
                                cm = cos_max if g <= gap else math.cos(math.radians(25.0))
                                if np.dot(da, -db) < cm: continue
                                if g > 0.3 and np.dot((pb - pa) / g, da) < cm: continue
                                if best is None or (g, i, li, j, lj) < best: best = (g, i, li, j, lj)
            if not best: break
            _, i, li, j, lj = best
            a = chains[i] if li else [q[::-1] for q in chains[i][::-1]]       # ends at the joint
            b = chains[j] if not lj else [q[::-1] for q in chains[j][::-1]]   # starts at the joint
            chains[i] = a + b; del chains[j]; del infos[j]; infos[i] = info(chains[i])
        return chains
    def split_at_symbols(chains, r=20.0):
        """A chain that runs on through a symbol is two wires that both end at it."""
        out = []
        for seq in chains:
            if straight(np.vstack(seq)):
                out.append(seq); continue                    # a straight dashed line (cabinet) stays whole
            cur = [seq[0]]
            total = sum(plen(q) for q in seq); before = plen(seq[0])
            for q in seq[1:]:
                a, b = cur[-1][-1], q[0]
                mid = (a + b) / 2
                h = near_hit(tuple(mid), r)
                # only where the wire carries on well past the symbol on both sides
                cut = h >= 0 and wire_ok[h] and (sum(plen(x) for x in cur) > 20 or straight(np.vstack(cur))) and total - before > 20
                before += plen(q)
                if cut:
                    out.append(cur); cur = [q]
                else:
                    cur.append(q)
            out.append(cur)
        return out
    sym_wires = []                   # (symbol, symbol, arc) for wires that run from one symbol to another
    def emit(f):
        if f[0] == 'arc':
            cx, cy, r, a0, a1 = f[1:]
            # to drawing units (y is flipped, so the arc runs the other way round)
            X, Y = tx(cx, cy); res.arcs.append(('wiring', float(X), float(Y), float(r * K), float((-a1) % 360), float((-a0) % 360)))
        else: res.lines.append(('wiring', [tx(*f[1]), tx(*f[2])], False))
    _ch = join_free_ends(split_at_symbols(wiring.chain(wire_pieces, gap=16.0)))
    for seq in _ch:
        P = np.vstack([seq[0]] + [q[1:] if np.hypot(*(q[0] - seq[0][-1])) < 0.05 else q for q in seq[1:]])
        length = float(np.sum(np.hypot(*np.diff(P, axis=0).T)))
        ia, ib = wire_hit(P[0]), wire_hit(P[-1])
        if float((P.max(axis=0) - P.min(axis=0)).max()) < 8 and near_hit(P.mean(axis=0), 28) >= 0:
            removed['stray letters'] += 1; continue          # bits of GFI / WP / 220 labels
        if len(seq) == 1:
            if length < 6 and ia < 0 and ib < 0:
                removed['stray letters'] += 1; continue      # a lone tick (a bit of a label over the wire)
            res.lines.append(('wiring', [tx(*v) for v in P], False)); continue   # a solid stroke, not a dashed wire: leave it
        # curved or dash-dot runs are wires; straight plain dashes are cabinet or soffit lines: keep them
        C = P[-1] - P[0]; Lc = float(np.hypot(*C))
        bow = float(np.abs((P - P[0]) @ (np.array([-C[1], C[0]]) / Lc)).max()) if Lc > 1 else 0.0
        curved = bow >= max(1.5, 0.04 * Lc)
        closed = Lc < 0.3 * length                          # loops back on itself (range burners, circles)
        # a run of plain straight strokes (one or two segments each), touching no symbol, is linework such as
        # an appliance outline: a wire's dashes follow its curve with many small segments
        def _loose(i, pt): return i < 0 or math.hypot(hits[i]['x'] - pt[0], hits[i]['y'] - pt[1]) > 12
        if _loose(ia, P[0]) and _loose(ib, P[-1]) and all(len(q) <= 3 for q in seq) and max(plen(q) for q in seq) > 6:
            for q in seq: res.lines.append(('misc', [tx(*v) for v in q], False))
            continue
        # a straight dashed run from one symbol to another is still a wire (cabinet lines don't end at symbols)
        OUTLETS = ('outlet', 'outlet_240', 'ceiling_outlet', 'floor_outlet')
        links = (ia >= 0 and ib >= 0 and ia != ib and length < 50 and (len(seq) <= 2 or dash_dot(seq))
                 and hits[ia]['cls'] not in OUTLETS and hits[ib]['cls'] not in OUTLETS)
        if (not curved and not dash_dot(seq) and not links) or (closed and ia < 0 and ib < 0):
            removed['dashed lines kept'] = removed.get('dashed lines kept', 0) + 1
            for q in seq: res.lines.append(('misc' if not curved else 'wiring', [tx(*v) for v in q], False))
            continue
        # a wire end that stops short, pointing at a symbol, ends at that symbol
        if curved and (ia < 0) != (ib < 0):                   # one end at a symbol, the other stops short
            if ia < 0: ia = ahead_hit(P[0], P[0] - P[min(3, len(P) - 1)])
            else: ib = ahead_hit(P[-1], P[-1] - P[max(-4, -len(P))])
        if ia >= 0 and ia == ib:
            continue                                         # both ends at one symbol: part of it
        if ia in hidden or ib in hidden:
            continue                                         # wire to a symbol this builder's plans leave off
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
        # a run lying along a word the text reader only partly read ('RINKLE' for SPRINKLER) is its letters, not a wire
        inside = np.zeros(len(P), bool)
        for b, w in zip(word_boxes_ext, words):
            if w['n'].startswith('TO'): continue              # wires do run up to 'TO LIGHT ABOVE'
            inside |= (P[:, 0] >= b[0]) & (P[:, 0] <= b[2]) & (P[:, 1] >= b[1]) & (P[:, 1] <= b[3])
        if len(P) and inside.mean() >= 0.6 and not forced:
            removed['text strokes'] = removed.get('text strokes', 0) + len(seq)
            continue
        def _attached(i, pt, inward):
            """The end reaches the symbol: right at it, or within reach and heading straight for it."""
            if i < 0: return False
            v = np.array([hits[i]['x'] - pt[0], hits[i]['y'] - pt[1]]); dd = float(np.hypot(*v))
            if dd <= 12: return True
            out = -inward / (float(np.hypot(*inward)) or 1.0)
            return float(out @ v) / dd > 0.8
        if not _attached(ia, P[0], P[min(3, len(P) - 1)] - P[0]) and not _attached(ib, P[-1], P[max(-4, -len(P))] - P[-1]) \
                and not forced and length < 60:
            for q in seq: res.lines.append(('misc', [tx(*v) for v in q], False))
            continue                                         # a wire always runs to a symbol or a note
        hA = np.array([hits[ia]['x'], hits[ia]['y']]) if ia >= 0 else None
        hB = np.array([hits[ib]['x'], hits[ib]['y']]) if ib >= 0 else None
        f = None
        tries = ((hA, hB), (hA, None), (None, hB), (None, None))
        if hA is not None and hB is not None and np.hypot(*(P[0] - hA)) <= 12 and np.hypot(*(P[-1] - hB)) <= 12:
            tries = tries[:1]       # both ends right at a symbol: the wire runs symbol to symbol, never stops short
        for A, B in tries:
            f = redraw(A if A is not None else P[0], B if B is not None else P[-1])
            if f and f[0] == 'arc' and A is not None and B is not None and abs((f[5] - f[4]) % 360) > 200:
                f = None            # a loop round the symbols: drawn as at most a half circle below
            if f: break
        if f is None and hA is not None and hB is not None:
            # the builder looped or wiggled between two symbols: still one clean arc between them,
            # bowing to the same side as the builder's wire (at most a half circle)
            AB = hB - hA; L = float(np.hypot(*AB))
            if L >= 1:
                nrm = np.array([-AB[1], AB[0]]) / L
                off = (P - hA) @ nrm; k = int(np.abs(off).argmax())
                sag = float(np.clip(off[k], -0.5 * L, 0.5 * L))
                if abs(sag) > 0.02 * L:
                    f = wiring.arc_through(tuple(hA), tuple((hA + hB) / 2 + nrm * sag), tuple(hB))
                else:
                    f = ('line', tuple(hA), tuple(hB))
        if f is None:                                        # nothing fits: keep the builder's wire, joined up
            res.lines.append(('wiring', [tx(*v) for v in P], False)); continue
        if f[0] == 'arc' and hA is not None and hB is not None:
            sym_wires.append((ia, ib, f)); continue          # drawn below, once wires that run together are matched up
        emit(f)
    # Wires from side-by-side symbols (a double switch) to one symbol bow the same way, as parallel arcs.
    groups = {}
    for k, (a, b, f) in enumerate(sym_wires):
        groups.setdefault(a, []).append(k); groups.setdefault(b, []).append(k)
    sag_of = {}
    def ends(k, shared):
        a, b, f = sym_wires[k]; other = b if a == shared else a
        return np.array([hits[shared]['x'], hits[shared]['y']]), np.array([hits[other]['x'], hits[other]['y']])
    def signed_sag(f, A, B):
        """Bulge of an arc from A to B: + to the left of A->B, - to the right, as a share of the chord."""
        cx, cy, r, a0, a1 = f[1:]
        L = float(np.hypot(*(B - A))); nrm = np.array([-(B - A)[1], (B - A)[0]]) / L
        mid = wiring.arc_points(cx, cy, r, a0, a1); m = mid[len(mid) // 2]
        return float((m - (A + B) / 2) @ nrm) / L
    for shared, ks in groups.items():
        if len(ks) < 2: continue
        others = [ends(k, shared)[1] for k in ks]
        # only wires whose far ends sit close together (a gang of switches), not every wire into a busy symbol
        close = [k for k, o in zip(ks, others) if all(np.hypot(*(o - o2)) < 20 for o2 in others)]
        if len(close) < 2: continue
        sags = [signed_sag(sym_wires[k][2], *ends(k, shared)) for k in close]
        ref = max(sags, key=abs)
        for k in close: sag_of.setdefault(k, (shared, ref))
    for k, (a, b, f) in enumerate(sym_wires):
        if k in sag_of:
            shared, ref = sag_of[k]
            A, B = ends(k, shared); L = float(np.hypot(*(B - A))); nrm = np.array([-(B - A)[1], (B - A)[0]]) / L
            f = wiring.arc_through(tuple(A), tuple((A + B) / 2 + nrm * ref * L), tuple(B))
        emit(f)

    # 6. symbols in drawing units, facing into the room
    for hi, h in enumerate(hits):
        if hi in hidden: continue
        X, Y = tx(h['x'], h['y'])
        s = Sym(cls=h['cls'], x=X, y=Y, score=round(h['score'], 3), mods=h['mods'], review=h.get('review', ''), src=(h['x'], h['y']), shape=h.get('shape', ''))
        d, (ux, uy) = facing(h['x'], h['y'], hint=hint_ends.get(hi, ()))
        s.dx, s.dy = ux, -uy
        if d <= 18: s.wall = d * K      # the block's insert point goes on this wall line (kept for every type: its type can change in review)
        if not s.review and h['score'] < 0.75: s.review = f"Low match ({h['score']:.2f})"
        if not s.review and s.cls in ORIENTED and d > 18: s.review = 'No wall nearby to face'
        fix = profile.turn_fix(s.cls)
        if fix: turn_sym(s, fix); s.fix = fix              # this builder's plans: people kept turning this type
        res.symbols.append(s)
    for w in rooms:
        X, Y = tx(w['cx'], w['cy']); res.texts.append(Text('room', [w['room']], X, Y))
    for w in notes:
        X, Y = tx(w['cx'], w['cy'])
        lines = [' '.join(NOTE_WORDS.get(t, t) for t in re.sub(r'\b(\d{3}) ?Y\b', r'\1V', ln).split()) for ln in w['lines']]   # 220Y -> 220V
        def scale(ln):
            m = re.match(r"SCALE:? *([0-9IO]+)/([0-9IO]+)", ln)
            if not m: return ln
            a, b = (re.sub('[IO]', lambda c: '1' if c.group() == 'I' else '0', g) for g in m.groups())
            return f'SCALE: {a}/{b}" = 1\'-0"'                  # 'SCALE I/4 I-O' -> SCALE: 1/4" = 1'-0"
        lines = [scale(rating_text(ln) if re.search(r'2[24]0 ?[VY]', ln) else ln) for ln in lines]
        lines = [re.sub(r'\((I|L|l)\)', '(1)', ' '.join(fix_word(t) for t in ln.split())) for ln in lines]   # '(I)' is (1)
        lines = [ln for ln in lines if ln.strip()]
        if not lines: continue
        res.texts.append(Text('note', lines, X, Y, review='Builder note kept: check it' if w['score'] < 0.7 else ''))
    # a note written level over a symbol (a rating the builder wrote sideways along a wall): move it clear
    NOTE_H = 3.0                                             # inches, about the usual note height
    syms = [(s.x, s.y) for s in res.symbols]
    for t in res.texts:
        if t.kind != 'note': continue
        hw = 0.6 * NOTE_H * max(len(ln) for ln in t.lines) / 2; hh = 0.85 * NOTE_H * len(t.lines)
        hit = [(x, y) for x, y in syms if abs(x - t.x) < hw + 4 and abs(y - t.y) < hh + 4]
        if hit:
            x, y = min(hit, key=lambda q: abs(q[1] - t.y))
            t.y = y + (hh + 8) * (1 if t.y > y else -1)
    if titles:
        titles.sort(key=lambda w: (w['dbb'][1], w['dbb'][0]))      # top line first, as the sheet reads
        lines = [title_text(w['n']) for w in titles]
        # the preview keeps it where the builder wrote it, like the room names;
        # sheet_layout() moves it (and the scale line) under the plan when the drawing is saved
        X, Y = tx(sum(w['cx'] for w in titles) / len(titles), sum(w['cy'] for w in titles) / len(titles))
        res.texts.append(Text('title', lines, X, Y))
    res.stats = dict(removed=removed, words=len(words), symbols=len(hits))
    return res
