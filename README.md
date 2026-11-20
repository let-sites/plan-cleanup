# Plan Cleanup

A desktop program (Windows and Mac) that turns a builder's electrical plan into a clean plan with **your** symbols.
It takes a builder **PDF** (vector, not scanned) or **DWG**, removes the clutter, swaps every builder symbol for
your block, and saves a DWG you can paste into your template.

## Install

1. **Download the program.** On GitHub, open **Actions → Build installers**, click the latest green run, and download
   `PlanCleanup-Windows` or `PlanCleanup-Mac` at the bottom. (Tagged versions also appear under **Releases**.)
   Unzip it anywhere.
   * Windows: run `PlanCleanup\PlanCleanup.exe`. If Windows warns about an unknown publisher, click
     **More info → Run anyway**.
   * Mac: drag `PlanCleanup.app` to Applications. The first time, **right-click → Open** (it isn't signed by Apple yet).
2. **Install the free ODA File Converter** from <https://www.opendesign.com/guestfiles/oda_file_converter>.
   Plan Cleanup uses it to read and write DWG files. Without it you can still open PDFs and save DXF.
3. **Open Settings** in Plan Cleanup once:
   * **Your block library**: any DWG that contains your blocks (for example one of your finished plan files).
   * **Builder profiles folder**: a shared folder (Dropbox, OneDrive, network drive) so everyone on the team uses
     the same builder setups.
   * Check which of your blocks is used for each symbol type (defaults: `standard_outlet_lisp`, `STANDARD SWTICH`,
     `standard_ceiling_light_lisp`, …).

## Using it

1. **Open plan…** and pick the electrical sheet on the left.
2. **Choose the builder** at the top (or **+ New builder…**).
3. **Plan area:** draw a box around the one plan you want. Leave out the legend, title block and option plans.
4. **Builder setup (only the first time for each builder):**
   * Check the drawing scale.
   * Line types: say which pens are walls, doors/windows, cabinets, electrical, other, or remove.
     Select a row to see those lines highlighted.
   * Click **Click a symbol to teach it**, then click one example of each builder symbol (outlet, switch, light,
     can, smoke detector, …) and choose what it is. Press Esc when done, then **Save builder setup**.
   * For a builder DWG you instead pick what each layer is and what each builder block is.
5. **Clean this plan.** Review the list of things that need a look (red rings). Click a marker to change its type,
   delete it, or mark it as fine. Click a note to edit it.
6. **Save cleaned plan…** as DWG, then paste it into your template.

The saved drawing uses layers `BASE-WALLS`, `BASE-DOORS-WINDOWS`, `BASE-CABINETS`, `BASE-MISC`, `ELEC-WIRING`,
`ELEC-SYMBOLS`, `ELEC-TEXT`, and `REVIEW` (red rings; freeze or delete it when you're done).

## What it does automatically

* Keeps walls, doors, windows, cabinets, stairs and switch wiring.
* Finds every builder symbol that looks like the examples you taught, and places your block turned toward the
  nearest wall.
* Reads the builder's text (even when it's plotted as lines), rewrites room names your way
  (e.g. `Garage (2)` → `2 CAR GARAGE`), removes labels like GFI, WP, DISK, 220V, CO/SD, PENDANTS and door sizes,
  and keeps other notes (`TO LIGHT ABOVE`) for you to check.
* Turns a ceiling light marked "BLK & WIRE FOR CLG. FAN" into a fan.
* Tells wall lights from ceiling lights by how close they are to a wall, and marks lights near "DISK" as disk lights.

## For developers

```
pip install -r requirements.txt
python run_plancleanup.py            # start the program
python -m pytest -q tests            # tests
pyinstaller plancleanup.spec         # build the app for the current OS
```

Code layout: `planclean/engine.py` (PDF cleanup), `dwgin.py` (DWG/DXF input), `detect.py` (symbol matching),
`ocr.py` (reading stroked text), `dxfout.py` (writing with your blocks), `oda.py` (DWG conversion),
`profile.py` (builder profiles and settings), `gui/` (the window). GitHub Actions builds the Windows and Mac apps
on every push.
