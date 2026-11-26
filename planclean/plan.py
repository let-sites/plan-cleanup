"""A Plan: the builder's files, and every area cleaned from them, saved so you can come back to it.

Each cleaned area is one item. Saving the plan writes all of its items into one drawing,
side by side in the order of the list.
"""
from __future__ import annotations
import copy, json, math, pickle, shutil, time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .profile import config_dir, safe_name
from .result import Result, Text, sheet_layout

GAP = 120.0          # inches between areas laid out side by side (10')


@dataclass
class PlanItem:
    label: str                 # what the list shows, e.g. "Electrical plan · Page 30"
    file: str                  # the builder's file it came from
    page: int                  # page index (0 for a DWG)
    kind: str                  # 'pdf' | 'dwg' (the source)
    region: tuple              # the box, in source units
    result_file: str           # pickled Result, inside the plan folder
    area_type: str = 'Electrical plan'
    elevation: str = ''          # which elevation's box it goes in on the template ('' = the first free one)
    level: str = ''              # 'Upper' | 'Main' | 'Lower' ('' = read from its title)
    added: float = 0.0


@dataclass
class Plan:
    name: str
    builder: str = ''
    files: list = field(default_factory=list)
    dwg_path: str = ''           # where the drawing was last saved
    template: str = ''           # your master template for this plan ('' = start blank); read fresh at every save
    project: str = ''            # fills PROJECT NAME on the template
    elevations: list = field(default_factory=list)   # fill the ELEVATION labels, e.g. ['Modern', 'Traditional']
    items: list = field(default_factory=list)
    folder: str = ''

    # ----- storage
    @staticmethod
    def plans_dir() -> Path:
        d = config_dir() / 'Plans'; d.mkdir(parents=True, exist_ok=True); return d

    @staticmethod
    def list_saved():
        """(name, folder, last changed, builder) for every saved plan, newest first."""
        out = []
        for f in Plan.plans_dir().glob('*/plan.json'):
            try:
                d = json.loads(f.read_text())
                out.append((d.get('name', f.parent.name), str(f.parent), f.stat().st_mtime, d.get('builder', '') or ''))
            except Exception: pass
        return sorted(out, key=lambda r: -r[2])

    @staticmethod
    def create(name, builder='', files=(), template=''):
        base = safe_name(name) or 'plan'; d = Plan.plans_dir() / base; n = 2
        while d.exists(): d = Plan.plans_dir() / f'{base} ({n})'; n += 1
        d.mkdir(parents=True)
        p = Plan(name=name, builder=builder or '', files=list(files), template=template or '', folder=str(d)); p.save(); return p

    @staticmethod
    def load(folder):
        d = json.loads((Path(folder) / 'plan.json').read_text())
        items = [PlanItem(**{k: v for k, v in it.items() if k in PlanItem.__dataclass_fields__}) for it in d.get('items', [])]
        for it in items: it.region = tuple(it.region)
        return Plan(name=d.get('name', Path(folder).name), builder=d.get('builder', ''), files=d.get('files', []),
                    dwg_path=d.get('dwg_path', ''), template=d.get('template', ''), project=d.get('project', ''),
                    elevations=d.get('elevations', []), items=items, folder=str(folder))

    def save(self):
        d = asdict(self); d.pop('folder')
        tmp = Path(self.folder) / 'plan.json.tmp'; tmp.write_text(json.dumps(d, indent=1)); tmp.replace(Path(self.folder) / 'plan.json')

    def delete(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    # ----- items
    def add_files(self, files):
        for f in files:
            if f not in self.files: self.files.append(f)
        self.save()

    def _store(self, res: Result) -> str:
        r = copy.copy(res); r.to_page = None; r.original = []          # the faded "removed" overlay isn't kept
        name = f'area-{int(time.time() * 1000)}.pkl'
        with open(Path(self.folder) / name, 'wb') as fh: pickle.dump(r, fh)
        return name

    def add(self, res, file, page, kind, region, label, area_type='Electrical plan', elevation='', level=''):
        it = PlanItem(label=label, file=file, page=page, kind=kind, region=tuple(region), result_file=self._store(res),
                      area_type=area_type, elevation=elevation, level=level, added=time.time())
        self.items.append(it); self.save(); return it

    def replace(self, i, res, region=None):
        it = self.items[i]; old = it.result_file
        it.result_file = self._store(res)
        if region is not None: it.region = tuple(region)
        self.save(); (Path(self.folder) / old).unlink(missing_ok=True)

    def remove(self, i):
        it = self.items.pop(i); self.save(); (Path(self.folder) / it.result_file).unlink(missing_ok=True)

    def move(self, i, j):
        if 0 <= j < len(self.items): self.items.insert(j, self.items.pop(i)); self.save()

    def result(self, it: PlanItem) -> Result:
        with open(Path(self.folder) / it.result_file, 'rb') as fh: return pickle.load(fh)


def reattach(res: Result, kind, profile, region):
    """Give a stored result back its link to the source sheet (for the preview)."""
    if kind == 'pdf':
        K, x0, y0 = profile.inches_per_pt, region[0], region[1]
        res.to_page = lambda X, Y: (X / K + x0, -Y / K + y0)
    else:
        x0, y0 = region[0], region[3]
        res.to_page = lambda X, Y: (X + x0, Y + y0)
    return res


def _extent(res: Result, texts):
    xs, ys = [], []
    for _, q, _ in res.lines:
        for x, y in q: xs.append(x); ys.append(y)
    for _, cx, cy, r, a0, a1 in res.arcs:                 # wires are shallow arcs: only the drawn part counts
        sw = (a1 - a0) % 360 or 360
        for k in range(9):
            a = math.radians(a0 + sw * k / 8); xs.append(cx + r * math.cos(a)); ys.append(cy + r * math.sin(a))
    for s in res.symbols: xs.append(s.x); ys.append(s.y)
    for t in texts:
        h = {'title': 9.0, 'room': 5.0}.get(t.kind, 3.0)
        w = 0.3 * h * max((len(l) for l in t.lines), default=0)
        xs += [t.x - w, t.x + w]; ys.append(t.y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def _place(out, res, texts, dx, dy):
    out.lines += [(role, [(x + dx, y + dy) for x, y in q], f) for role, q, f in res.lines]
    out.arcs += [(role, cx + dx, cy + dy, r, a0, a1) for role, cx, cy, r, a0, a1 in res.arcs]
    for s in res.symbols:
        s = copy.copy(s); s.x += dx; s.y += dy; out.symbols.append(s)
    for t in texts:
        out.texts.append(Text(t.kind, list(t.lines), t.x + dx, t.y + dy, t.review))


def combine(results, title_h=9.0) -> Result:
    """All areas in one drawing: side by side, left to right, tops lined up. Each area's own
    title is already set under it, so the texts come back laid out."""
    out = Result(); cursor = 0.0
    for res in results:
        texts = sheet_layout(res, title_h)
        x0, y0, x1, y1 = _extent(res, texts)
        _place(out, res, texts, cursor - x0, -y1)
        cursor += (x1 - x0) + GAP
    return out


# ---------- your MASTER FILE TEMPLATE: a board in model space with labelled boxes

def _plain(raw):
    import re
    t = re.sub(r'\\[A-Za-z][^;\\]*;', '', raw)          # \pxqc; \fArial|b0; ...
    return re.sub(r'[{}]', '', t).replace('\\P', ' ').strip()


def find_board(doc):
    """The template's boxes, found from its labels and the lines around them.
    Returns None when the drawing isn't laid out that way."""
    msp = doc.modelspace()
    H, V = [], []                                         # (y, x0, x1) and (x, y0, y1)
    def seg(a, b):
        if abs(a[1] - b[1]) < 1e-3: H.append((a[1], min(a[0], b[0]), max(a[0], b[0])))
        elif abs(a[0] - b[0]) < 1e-3: V.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
    for e in msp:
        t = e.dxftype()
        if t == 'LINE': seg((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))
        elif t == 'LWPOLYLINE':
            pts = [(p[0], p[1]) for p in e.get_points('xy')]
            if e.closed and pts: pts.append(pts[0])
            for a, b in zip(pts, pts[1:]): seg(a, b)
    labels = []
    for e in msp.query('MTEXT'):
        raw = e.text; h = e.dxf.char_height; w = e.dxf.get('width', 0) or 0
        x, y = e.dxf.insert.x, e.dxf.insert.y; att = e.dxf.get('attachment_point', 1)
        col = (att - 1) % 3; row = (att - 1) // 3
        left = x - (w / 2 if col == 1 else w if col == 2 else 0)
        cx = left + w / 2 if (w and ('\\pxqc' in raw or col == 1)) else x
        cy = y - h / 2 if row == 0 else y if row == 1 else y + h / 2
        labels.append((_plain(raw).upper(), cx, cy, e))
    def below(x, y):                                     # first box line under (x, y)
        ys = [hy for hy, a, b in H if a - 1 <= x <= b + 1 and hy < y]
        return max(ys) if ys else None
    def cell(x, y):
        l = [vx for vx, a, b in V if a - 1 <= y <= b + 1 and vx < x]; r = [vx for vx, a, b in V if a - 1 <= y <= b + 1 and vx > x]
        d = [hy for hy, a, b in H if a - 1 <= x <= b + 1 and hy < y]; u = [hy for hy, a, b in H if a - 1 <= x <= b + 1 and hy > y]
        if not (l and r and d and u): return None
        return (max(l), max(d), min(r), min(u))
    elev = sorted((l for l in labels if l[0] == 'ELEVATION'), key=lambda l: l[1])
    cols = []
    for _, x, y, e in elev:
        yb = below(x, y)
        c = cell(x, yb - 1) if yb is not None else None
        if c: cols.append((c, e))
    opt = next((l for l in labels if l[0] == 'OPTIONS'), None)
    opt_box = None
    if opt:
        yb = below(opt[1], opt[2])
        opt_box = cell(opt[1], yb - 1) if yb is not None else None
    if not cols and not opt_box: return None
    named = {k: next((l[3] for l in labels if l[0] == k), None) for k in ('PLAN NAME', 'PROJECT NAME')}
    return dict(columns=cols, options=opt_box, plan_name=named['PLAN NAME'], project_name=named['PROJECT NAME'])


def _set_label(e, text):
    """Replace a label's words, keeping its formatting (e.g. centred)."""
    import re
    raw = e.text; m = re.match(r'^((?:\\[A-Za-z][^;\\]*;)*)', raw)
    e.text = (m.group(1) if m else '') + text.replace('\\', '/')


LEVELS = ['Upper', 'Main', 'Lower']
LEVEL_GAP = 240.0      # inches between the levels of one plan (20')


def is_option(res):
    """The sheet's title calls it an option: OPT. CHEF'S KITCHEN, OPTIONAL BEDROOM 5, ..."""
    import re
    t = ' '.join(' '.join(x.lines) for x in res.texts if x.kind == 'title').upper()
    return bool(re.search(r"\bOPT(\.|ION|IONAL)?\b", t))


def level_rank(res):
    """Where a level goes in a row of levels: upper floors first, then the main floor, then the lower ones."""
    import re
    t = ' '.join(' '.join(x.lines) for x in res.texts if x.kind == 'title').upper()
    if re.search(r'\b(UPPER|SECOND|2ND|THIRD|3RD|LOFT|ATTIC)\b', t): return 0
    if re.search(r'\b(BASEMENT|LOWER|LL|CELLAR|WALK-?OUT|GARDEN LEVEL)\b', t): return 2
    return 1


def on_board(board, plan_results, option_results, title_h=9.0, project='', plan_name='', elevations=()):
    """Main plans centred in the boxes under the elevation names (one per elevation, in order);
    options packed into the OPTIONS box, left to right, top to bottom. Labels filled in."""
    out = Result(); spill = []
    cols = board['columns']
    # plan_results: results, or (result, elevation name) pairs; a named elevation takes its own box
    # plan_results: results, or (result, elevation) / (result, elevation, level) tuples
    pairs = [(p + ('',))[:3] if isinstance(p, tuple) and len(p) == 2 else p if isinstance(p, tuple) else (p, '', '') for p in plan_results]
    names = [n.strip().upper() for n in elevations]
    # a named elevation takes its own box; plans with no elevation are the levels of the main plan and share
    # the first box that's left. Levels sit side by side: upper, main, then lower.
    groups = {}
    rank = {}
    for res, ev, lv in pairs:
        rank[id(res)] = LEVELS.index(lv) if lv in LEVELS else level_rank(res)
        k = names.index(ev.strip().upper()) if ev.strip() and ev.strip().upper() in names else None
        groups.setdefault(k if k is not None and k < len(cols) else None, []).append(res)
    loose = groups.pop(None, [])
    if loose:
        free = [c for c in range(len(cols)) if c not in groups]
        if free: groups[free[0]] = loose
        else: spill += loose
    for k, rs in groups.items():
        rs = sorted(rs, key=lambda r: rank[id(r)])
        laid = [(r, sheet_layout(r, title_h)) for r in rs]
        ext = [_extent(r, t) for r, t in laid]
        width = sum(e[2] - e[0] for e in ext) + LEVEL_GAP * (len(ext) - 1)
        top = max(e[3] for e in ext); bot = min(top - (e[3] - e[1]) for e in ext)
        cx0, cy0, cx1, cy1 = cols[k][0]
        x = (cx0 + cx1) / 2 - width / 2; ty = (cy0 + cy1) / 2 + (top - bot) / 2      # tops lined up, the group centred
        for (r, t), e in zip(laid, ext):
            _place(out, r, t, x - e[0], ty - e[3]); x += (e[2] - e[0]) + LEVEL_GAP
    box = board['options']
    if box is None and option_results: spill += option_results
    elif option_results:
        bx0, by0, bx1, by1 = box; pad = 120.0
        x, top, row_h = bx0 + pad, by1 - pad, 0.0
        for res in option_results:
            texts = sheet_layout(res, title_h); x0, y0, x1, y1 = _extent(res, texts)
            w, h = x1 - x0, y1 - y0
            if x > bx0 + pad and x + w > bx1 - pad:             # next row
                x = bx0 + pad; top -= row_h + GAP; row_h = 0.0
            _place(out, res, texts, x - x0, top - y1)
            x += w + GAP; row_h = max(row_h, h)
    if plan_name and board.get('plan_name') is not None: _set_label(board['plan_name'], plan_name.upper())
    if project and board.get('project_name') is not None: _set_label(board['project_name'], project.upper())
    for name, (_, e) in zip(elevations, cols):
        if name.strip(): _set_label(e, name.strip().upper())
    return out, spill
