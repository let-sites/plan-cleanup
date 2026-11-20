"""The symbol types the tool knows about and their default blocks."""
# key: (label shown to the user, default block in your library, turned toward the wall?)
SYMBOL_TYPES = {
    "outlet":         ("Outlet (any: standard, GFI, WP, 220)", "standard_outlet_lisp", True),
    "switch":         ("Switch (single or 3-way)",            "STANDARD SWTICH", True),
    "dimmer":         ("Dimmer switch",                        "standard_dimmerswitch_lisp", True),
    "ceiling_light":  ("Ceiling light",                        "standard_ceiling_light_lisp", False),
    "wall_light":     ("Wall light / sconce",                  "wall_light_lisp", True),
    "recessed_can":   ("Recessed can",                         "can_lisp", False),
    "disk_light":     ("Disk light",                           "standard_disklight_lisp", False),
    "fan":            ("Ceiling fan",                          "standard_fan_lisp", False),
    "exhaust_fan":    ("Exhaust fan",                          "standard_exhaust_lisp", False),
    "smoke":          ("Smoke detector",                       "SMOKE", False),
    "co_smoke":       ("Smoke / CO detector",                  "SMOKE_CO", False),
    "ceiling_outlet": ("Ceiling outlet (garage door etc.)",   "standard_outlet_lisp", False),
    "floor_outlet":   ("Floor outlet",                         "standard_floor_outlet_lisp", False),
    "flood_light":    ("Flood light",                          "flood_light_lisp", True),
    "jbox":           ("J-box",                                "", False),
    "remove":         ("Remove it (don't replace)",            "", False),
}
ORIENTED = {k for k, v in SYMBOL_TYPES.items() if v[2]}
LIGHTS = {"ceiling_light", "wall_light"}

def label(cls):
    return SYMBOL_TYPES.get(cls, (cls,))[0]

PEN_ROLES = {
    "wall": "Walls",
    "opening": "Doors, windows, fixtures",
    "casework": "Cabinets, stairs",
    "electrical": "Electrical (symbols, wiring, notes)",
    "misc": "Other linework (keep)",
    "remove": "Remove",
}
