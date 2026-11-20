import sys, os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QRectF
from planclean.gui.main import MainWindow
from planclean.engine import clean_pdf
from planclean.profile import Settings
pdf, lib, shot = sys.argv[1:4]
app = QApplication([])
s = Settings.load(); s.block_library = lib; s.save()
w = MainWindow(); w.resize(1600, 1000); w.show()
w.set_builder('American Legend'); w.load(pdf, 35)
# region drawn in view coords: map the known page region
x0, y0, x1, y1 = 110, 1130, 905, 2195
a = w.sheet.to_view(x0, y0); b = w.sheet.to_view(x1, y1)
r = QRectF(min(a[0], b[0]), min(a[1], b[1]), abs(a[0] - b[0]), abs(a[1] - b[1]))
w.view.show_region(r); w.region = w.sheet.region_from_view(r)
app.processEvents(); w.grab().save(shot.replace('.png', '_setup.png'))
res = clean_pdf(w.sheet.page, w.region, w.profile)
w.on_cleaned(res)
app.processEvents(); w.grab().save(shot)
print('region', [round(v) for v in w.region], 'symbols', len(res.symbols), 'review', w.review.count())
