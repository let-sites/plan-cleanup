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
    added: float = 0.0


@dataclass
class Plan:
    name: str
    builder: str = ''
    files: list = field(default_factory=list)
    dwg_path: str = ''           # where the drawing was last saved
    template: str = ''           # your master template for this plan ('' = start blank); read fresh at every save
    items: list = field(default_factory=list)
    folder: str = ''

    # ----- storage
    @staticmethod
    def plans_dir() -> Path:
        d = config_dir() / 'Plans'; d.mkdir(parents=True, exist_ok=True); return d

    @staticmethod
    def list_saved():
        """(name, folder, last changed) for every saved plan, newest first."""
        out = []
        for f in Plan.plans_dir().glob('*/plan.json'):
            try: out.append((json.loads(f.read_text()).get('name', f.parent.name), str(f.parent), f.stat().st_mtime))
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
                    dwg_path=d.get('dwg_path', ''), template=d.get('template', ''), items=items, folder=str(folder))

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

    def add(self, res, file, page, kind, region, label, area_type='Electrical plan'):
        it = PlanItem(label=label, file=file, page=page, kind=kind, region=tuple(region), result_file=self._store(res),
                      area_type=area_type, added=time.time())
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


def combine(results, title_h=9.0) -> Result:
    """All areas in one drawing: side by side, left to right, tops lined up. Each area's own
    title is already set under it, so the texts come back laid out."""
    out = Result(); cursor = 0.0
    for res in results:
        texts = sheet_layout(res, title_h)
        x0, y0, x1, y1 = _extent(res, texts)
        dx, dy = cursor - x0, -y1
        out.lines += [(role, [(x + dx, y + dy) for x, y in q], f) for role, q, f in res.lines]
        out.arcs += [(role, cx + dx, cy + dy, r, a0, a1) for role, cx, cy, r, a0, a1 in res.arcs]
        for s in res.symbols:
            s = copy.copy(s); s.x += dx; s.y += dy; out.symbols.append(s)
        for t in texts:
            out.texts.append(Text(t.kind, list(t.lines), t.x + dx, t.y + dy, t.review))
        cursor += (x1 - x0) + GAP
    return out
