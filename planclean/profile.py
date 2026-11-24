"""Builder profiles (how one builder draws) and app settings (your blocks)."""
from __future__ import annotations
import base64, json, os, re, uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
import numpy as np, cv2

DEFAULT_ROOMS = {
    "FAMILY RM.": "FAMILY ROOM", "FAMILY ROOM": "FAMILY ROOM", "GREAT RM.": "GREAT ROOM", "GREAT ROOM": "GREAT ROOM",
    "LIVING": "LIVING ROOM", "LIVING RM.": "LIVING ROOM", "DINING": "DINING", "KITCHEN": "KITCHEN", "NOOK": "NOOK",
    "PANTRY": "PANTRY", "PDR.": "POWDER", "POWDER": "POWDER", "MUD": "MUD ROOM", "MUD RM.": "MUD ROOM",
    "FOYER": "FOYER", "ENTRY": "ENTRY", "STUDY": "STUDY", "OFFICE": "OFFICE", "DEN": "DEN", "FLEX": "FLEX",
    "PORCH": "PORCH", "COVERED PATIO": "COVERED PATIO", "PATIO": "PATIO", "DECK": "DECK",
    "TANDEM": "TANDEM", "GARAGE": "GARAGE", "GARAGE (2)": "2 CAR GARAGE", "GARAGE (3)": "3 CAR GARAGE",
    "MAIN BEDROOM": "MAIN BEDROOM", "OWNER'S SUITE": "OWNER'S SUITE", "BEDROOM": "BEDROOM",
    "BEDROOM 2": "BEDROOM 2", "BEDROOM 3": "BEDROOM 3", "BEDROOM 4": "BEDROOM 4", "BEDROOM 5": "BEDROOM 5",
    "M. BATH": "MAIN BATH", "MAIN BATH": "MAIN BATH", "BATH": "BATH", "BATH 2": "BATH 2", "BATH 3": "BATH 3",
    "W.I.C.": "W.I.C.", "LAUNDRY": "LAUNDRY", "UTILITY": "UTILITY", "LOFT": "LOFT", "GAMEROOM": "GAME ROOM",
    "MECH.": "MECH.", "REC ROOM": "REC ROOM", "STORAGE": "STORAGE",
    "GATHERING ROOM": "GATHERING ROOM", "GATHERING": "GATHERING ROOM", "CAFE": "CAFE", "OWNER'S BATH": "OWNER'S BATH",
    "OWNER'S BEDROOM": "OWNER'S BEDROOM", "DINING ROOM": "DINING", "BREAKFAST": "BREAKFAST", "LIBRARY": "LIBRARY",
}
DEFAULT_DROP = [r"^GFI$", r"^WP/?GFI$", r"^WP$", r"^DISK$", r"^220V?$", r"^CLG\.?$", r"^PENDANTS?$",
                r"^CO/SD\.?$", r"^SD/CO\.?$", r"^SD$", r"^\d{4} ?SR[O0]$", r"^E-?PANEL$", r"^EMER\.?$", r"^DISCONNECT$"]
NOTE_WORDS = {"ABV": "ABOVE", "ABY": "ABOVE", "AB": "ABOVE", "BLW": "BELOW",
              "TOFAN": "TO FAN", "TOLIGHT": "TO LIGHT", "SFCI": "GFCI", "EY": "EV"}   # common misreads of the builder's short notes

def img_to_b64(img):
    ok, buf = cv2.imencode('.png', img.astype(np.uint8)); return base64.b64encode(buf.tobytes()).decode()

def b64_to_img(s):
    return cv2.imdecode(np.frombuffer(base64.b64decode(s), np.uint8), cv2.IMREAD_GRAYSCALE)

@dataclass
class SymbolExample:
    cls: str                 # symbol type key (see symbols.py)
    kind: str                # 'circle' | 'glyph' | 'scan'
    half: float              # half window size in points
    rotations: list          # 90 degree turns to try
    template: str            # base64 PNG at Profile.TEMPLATE_SCALE px/pt
    threshold: float = 0.6
    half_y: float = 0.0      # half window height when it isn't square (a skinny symbol); 0 = same as half
    learned: bool = False    # saved from the review (Change type / Looks right), not taught
    where: list = field(default_factory=list)   # [file, page, x, y] where it was taught (page points), to show it again
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def image(self):
        return b64_to_img(self.template).astype(np.float32)

@dataclass
class Profile:
    name: str
    inches_per_pt: float = 48 / 72          # 1/4" = 1'-0"
    pen_roles: dict = field(default_factory=dict)
    symbols: list = field(default_factory=list)
    room_names: dict = field(default_factory=lambda: dict(DEFAULT_ROOMS))
    drop_labels: list = field(default_factory=lambda: list(DEFAULT_DROP))
    wall_light_max_dist: float = 12.0       # points from a wall line
    room_text_min_size: float = 10.0        # text height in points
    dwg_block_map: dict = field(default_factory=dict)   # builder DWG block name -> symbol type
    dwg_layer_roles: dict = field(default_factory=dict) # builder DWG layer -> pen role
    # what LET's finished plan shows for this builder
    outlets: str = 'all'                    # 'all' | 'outside' (exterior and garage only) | 'none'
    detectors: bool = True                  # smoke and smoke/CO detectors
    exhaust_fans: bool = True
    TEMPLATE_SCALE = 6

    @staticmethod
    def load(path):
        d = json.loads(Path(path).read_text())
        d['symbols'] = [SymbolExample(**{k: v for k, v in s.items() if k in SymbolExample.__dataclass_fields__}) for s in d.get('symbols', [])]
        for ex in d['symbols']:
            if ex.cls == 'jbox': ex.cls = 'ceiling_light'     # the J-box type was retired
        if isinstance(d.get('room_names'), dict): d['room_names'] = {**DEFAULT_ROOMS, **d['room_names']}   # rooms added since it was made
        p = Profile(**{k: v for k, v in d.items() if k in Profile.__dataclass_fields__})
        return p

    def save(self, path):
        d = asdict(self)
        Path(path).write_text(json.dumps(d, indent=1))

    def compiled_drop(self):
        return [re.compile(p) for p in self.drop_labels]

def safe_name(name):
    return re.sub(r'[^A-Za-z0-9_. -]+', '_', name).strip() or 'builder'

def config_dir():
    if os.name == 'nt':
        base = Path(os.environ.get('APPDATA', Path.home()))
    elif os.uname().sysname == 'Darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    d = base / 'PlanCleanup'; d.mkdir(parents=True, exist_ok=True); return d

@dataclass
class Settings:
    profiles_folder: str = ""      # shared folder (Dropbox / network drive) for builder profiles
    block_library: str = ""        # DWG or DXF that holds your blocks
    block_map: dict = field(default_factory=dict)   # symbol type -> your block name (overrides defaults)
    oda_path: str = ""             # ODA File Converter executable (auto-detected when empty)
    text_height_room: float = 5.0  # inches
    text_height_note: float = 3.0
    text_height_title: float = 9.0  # the sheet / option title
    text_style: str = "Notes"      # Arial
    text_layer: str = "$RoomNames" # room names and kept notes
    symbol_layer: str = "$Standard"
    master_template: str = ""      # your MASTER FILE TEMPLATE; new plans start from it
    settings_version: int = 4

    @staticmethod
    def load():
        f = config_dir() / 'settings.json'
        if f.exists():
            d = json.loads(f.read_text())
            if d.get('settings_version', 1) < 3:
                # earlier versions had other defaults; your standard is 5" Notes (white) on $Circuits
                d.pop('text_height_room', None); d.pop('text_height_note', None)
                d['settings_version'] = 3
            if d.get('settings_version', 1) < 4:
                if d.get('text_layer') == '$Circuits': d['text_layer'] = '$RoomNames'     # your text layer
                d['settings_version'] = 4
            return Settings(**{k: v for k, v in d.items() if k in Settings.__dataclass_fields__})
        return Settings(profiles_folder=str(config_dir() / 'profiles'))

    def save(self):
        (config_dir() / 'settings.json').write_text(json.dumps(asdict(self), indent=1))

    def profiles_dir(self):
        d = Path(self.profiles_folder or (config_dir() / 'profiles')); d.mkdir(parents=True, exist_ok=True); return d

    def list_profiles(self):
        return sorted(p.stem for p in self.profiles_dir().glob('*.json'))

    def profile_path(self, name):
        return self.profiles_dir() / (safe_name(name) + '.json')

    def block_for(self, cls):
        from .symbols import SYMBOL_TYPES
        return self.block_map.get(cls) or SYMBOL_TYPES.get(cls, ("", ""))[1]
