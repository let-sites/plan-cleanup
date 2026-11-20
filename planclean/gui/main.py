"""Plan Cleanup main window."""
from __future__ import annotations
import os, sys, traceback, tempfile
from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QThread, Signal, QObject, QPointF, QRectF
from PySide6.QtGui import QAction, QColor, QPixmap, QImage, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog, QListWidget, QListWidgetItem, QDockWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QInputDialog, QProgressDialog, QCheckBox, QToolBar, QLineEdit, QFormLayout, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QAbstractItemView, QSplitter)

from planclean import __version__
from planclean.profile import Profile, Settings, safe_name
from planclean.symbols import SYMBOL_TYPES, PEN_ROLES, label as sym_label
from planclean.engine import clean_pdf, pens_in, guess_roles
from planclean.teach import Teacher
from planclean.vec import in_region, pen_key
from planclean import oda, dxfout, dwgin
from planclean.result import Result
from .view import SheetView, CLASS_COLORS
from .sources import PdfSheet, DwgSheet

SCALES = [('1/4" = 1\'-0"', 48), ('1/8" = 1\'-0"', 96), ('3/16" = 1\'-0"', 64), ('3/8" = 1\'-0"', 32), ('1/2" = 1\'-0"', 24)]

class Worker(QObject):
    done = Signal(object); failed = Signal(str); progress = Signal(str)
    def __init__(self, fn): super().__init__(); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn(self.progress.emit))
        except Exception: self.failed.emit(traceback.format_exc())

def run_in_thread(parent, fn, on_done, title):
    dlg = QProgressDialog(title, None, 0, 0, parent); dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0); dlg.show()
    th = QThread(parent); w = Worker(fn); w.moveToThread(th)
    th.started.connect(w.run)
    w.progress.connect(dlg.setLabelText)
    def fin(res): dlg.close(); th.quit(); on_done(res)
    def err(msg): dlg.close(); th.quit(); QMessageBox.critical(parent, 'Something went wrong', msg[-2000:])
    w.done.connect(fin); w.failed.connect(err)
    parent._threads = getattr(parent, '_threads', []) + [(th, w)]
    th.start()

class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent); self.setWindowTitle('Settings'); self.s = settings
        f = QFormLayout(self)
        self.prof = QLineEdit(settings.profiles_folder); b1 = QPushButton('Choose…'); b1.clicked.connect(lambda: self._dir(self.prof))
        self.lib = QLineEdit(settings.block_library); b2 = QPushButton('Choose…'); b2.clicked.connect(lambda: self._file(self.lib, 'Drawings (*.dwg *.dxf)'))
        self.oda = QLineEdit(settings.oda_path or (oda.find_converter() or '')); b3 = QPushButton('Choose…'); b3.clicked.connect(lambda: self._file(self.oda, 'Programs (*)'))
        for lab, ed, b, tip in [('Builder profiles folder', self.prof, b1, 'Put this in a shared folder (Dropbox, network drive) so everyone uses the same builder setups.'),
                                ('Your block library (DWG/DXF)', self.lib, b2, 'A drawing that contains your standard blocks, e.g. one of your plan files.'),
                                ('ODA File Converter', self.oda, b3, 'Free download from opendesign.com. Needed to read and write DWG.')]:
            row = QHBoxLayout(); row.addWidget(ed); row.addWidget(b); w = QWidget(); w.setLayout(row); w.setToolTip(tip); f.addRow(lab, w)
        self.th_room = QDoubleSpinBox(); self.th_room.setValue(settings.text_height_room); self.th_room.setSuffix(' in')
        self.th_note = QDoubleSpinBox(); self.th_note.setValue(settings.text_height_note); self.th_note.setSuffix(' in')
        f.addRow('Room name text height', self.th_room); f.addRow('Note text height', self.th_note)
        f.addRow(QLabel('Which of your blocks to use for each symbol type:'))
        self.table = QTableWidget(len(SYMBOL_TYPES) - 1, 2); self.table.setHorizontalHeaderLabels(['Symbol type', 'Your block'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.table.verticalHeader().hide()
        blocks = parent.library_blocks() if parent else []
        self.combos = {}
        for i, (k, (lab, default, _)) in enumerate([kv for kv in SYMBOL_TYPES.items() if kv[0] != 'remove']):
            self.table.setItem(i, 0, QTableWidgetItem(lab))
            cb = QComboBox(); cb.setEditable(True); cb.addItems(sorted(set(blocks + [settings.block_for(k)])))
            cb.setCurrentText(settings.block_for(k)); self.table.setCellWidget(i, 1, cb); self.combos[k] = cb
        self.table.setMinimumHeight(300); f.addRow(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); f.addRow(bb)
        self.resize(640, 700)
    def _dir(self, ed):
        d = QFileDialog.getExistingDirectory(self, 'Choose folder', ed.text())
        if d: ed.setText(d)
    def _file(self, ed, flt):
        f, _ = QFileDialog.getOpenFileName(self, 'Choose file', ed.text(), flt)
        if f: ed.setText(f)
    def apply(self):
        s = self.s; s.profiles_folder = self.prof.text(); s.block_library = self.lib.text(); s.oda_path = self.oda.text()
        s.text_height_room = self.th_room.value(); s.text_height_note = self.th_note.value()
        s.block_map = {k: cb.currentText() for k, cb in self.combos.items() if cb.currentText() != SYMBOL_TYPES[k][1]}
        s.save()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Plan Cleanup {__version__}')
        self.settings = Settings.load()
        self.sheet = None; self.sheets = []; self.region = None; self.profile = None
        self.teacher = None; self.result: Result | None = None; self.dwg_doc = None; self._lib = None
        self.view = SheetView(); self.setCentralWidget(self.view)
        self.view.regionDrawn.connect(self.on_region); self.view.clicked.connect(self.on_teach_click); self.view.itemPicked.connect(self.on_pick)
        self._toolbar(); self._docks()
        self.resize(1500, 950)
        self.statusBar().showMessage('Open a builder PDF or DWG to start.')

    # ---------- layout
    def _toolbar(self):
        tb = QToolBar('Main'); tb.setMovable(False); self.addToolBar(tb)
        a = QAction('Open plan…', self); a.setShortcut(QKeySequence.Open); a.triggered.connect(self.open_file); tb.addAction(a)
        tb.addSeparator(); tb.addWidget(QLabel(' Builder: '))
        self.builder_cb = QComboBox(); self.builder_cb.setMinimumWidth(220); self.builder_cb.activated.connect(self.on_builder); tb.addWidget(self.builder_cb)
        self.refresh_builders()
        tb.addSeparator()
        a = QAction('Settings…', self); a.triggered.connect(self.open_settings); tb.addAction(a)

    def _docks(self):
        self.pages = QListWidget(); self.pages.currentRowChanged.connect(self.on_page)
        d = QDockWidget('Sheets', self); d.setWidget(self.pages); d.setFeatures(QDockWidget.NoDockWidgetFeatures); self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.tabs = QTabWidget(); self.tabs.setMinimumWidth(430)
        self.tabs.addTab(self._tab_area(), '1  Plan area')
        self.tabs.addTab(self._tab_setup(), '2  Builder setup')
        self.tabs.addTab(self._tab_review(), '3  Clean && review')
        d = QDockWidget('Steps', self); d.setWidget(self.tabs); d.setFeatures(QDockWidget.NoDockWidgetFeatures); self.addDockWidget(Qt.RightDockWidgetArea, d)

    def _tab_area(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._help('Pick the electrical sheet on the left, then draw a box around the one plan you want to clean. '
                               'Leave out the legend, title block and option plans.'))
        b = QPushButton('Draw box around the plan'); b.clicked.connect(lambda: self.view.set_mode('box')); v.addWidget(b)
        b = QPushButton('Pan / zoom (mouse wheel)'); b.clicked.connect(lambda: self.view.set_mode('pan')); v.addWidget(b)
        self.area_lbl = QLabel('No plan area yet.'); v.addWidget(self.area_lbl); v.addStretch(); return w

    def _tab_setup(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._help('Do this once per builder. Tell the tool what each line type is, then click one example of each '
                               'symbol and choose what it is. Save when done; later plans from this builder skip this step.'))
        row = QHBoxLayout(); row.addWidget(QLabel('Drawing scale')); self.scale_cb = QComboBox(); self.scale_cb.addItems([s[0] for s in SCALES])
        self.scale_cb.currentIndexChanged.connect(self.on_scale); row.addWidget(self.scale_cb); v.addLayout(row)
        self.roles_lbl = QLabel('Line types on this plan'); v.addWidget(self.roles_lbl)
        self.roles = QTableWidget(0, 3); self.roles.setHorizontalHeaderLabels(['Line type', 'Count', 'What it is'])
        self.roles.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.roles.verticalHeader().hide()
        self.roles.itemSelectionChanged.connect(self.on_role_select); v.addWidget(self.roles, 2)
        self.sym_lbl = QLabel('Symbol examples'); v.addWidget(self.sym_lbl)
        self.examples = QTableWidget(0, 2); self.examples.setHorizontalHeaderLabels(['Builder symbol', 'Is a…'])
        self.examples.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch); self.examples.verticalHeader().hide()
        self.examples.setIconSize(QPixmap(48, 48).size()); v.addWidget(self.examples, 2)
        row = QHBoxLayout()
        self.teach_btn = QPushButton('Click a symbol to teach it'); self.teach_btn.clicked.connect(self.start_teach); row.addWidget(self.teach_btn)
        b = QPushButton('Remove selected'); b.clicked.connect(self.remove_example); row.addWidget(b); v.addLayout(row)
        b = QPushButton('Save builder setup'); b.clicked.connect(self.save_profile); v.addWidget(b)
        return w

    def _tab_review(self):
        w = QWidget(); v = QVBoxLayout(w)
        b = QPushButton('Clean this plan'); b.setStyleSheet('font-weight:bold;padding:8px'); b.clicked.connect(self.run_clean); v.addWidget(b)
        self.summary = QLabel(''); self.summary.setWordWrap(True); v.addWidget(self.summary)
        self.show_orig = QCheckBox('Show the builder sheet underneath'); self.show_orig.setChecked(False); self.show_orig.toggled.connect(self.draw_result); v.addWidget(self.show_orig)
        v.addWidget(QLabel('Needs a look (click to jump; click a marker on the plan to edit it):'))
        self.review = QListWidget(); self.review.itemClicked.connect(self.on_review_item); v.addWidget(self.review, 3)
        row = QHBoxLayout(); self.edit_type = QComboBox(); self.edit_type.addItems([v_[0] for v_ in SYMBOL_TYPES.values()])
        self.edit_type.activated.connect(self.change_type); row.addWidget(self.edit_type)
        b = QPushButton('Delete'); b.clicked.connect(self.delete_sel); row.addWidget(b)
        b = QPushButton('Looks right'); b.clicked.connect(self.ok_sel); row.addWidget(b); v.addLayout(row)
        b = QPushButton('Save cleaned plan…'); b.setStyleSheet('font-weight:bold;padding:8px'); b.clicked.connect(self.save_result); v.addWidget(b)
        return w

    def _help(self, text):
        l = QLabel(text); l.setWordWrap(True); l.setStyleSheet('color:#444;background:#f4f6f8;padding:8px;border-radius:4px'); return l

    # ---------- builders
    def refresh_builders(self, select=None):
        self.builder_cb.clear(); self.builder_cb.addItem('— choose builder —')
        for n in self.settings.list_profiles(): self.builder_cb.addItem(n)
        self.builder_cb.addItem('+ New builder…')
        if select:
            i = self.builder_cb.findText(select)
            if i >= 0: self.builder_cb.setCurrentIndex(i)

    def on_builder(self, idx):
        txt = self.builder_cb.itemText(idx)
        if txt.startswith('+'):
            name, ok = QInputDialog.getText(self, 'New builder', 'Builder name:')
            if not ok or not name.strip(): self.builder_cb.setCurrentIndex(0); return
            self.profile = Profile(name=name.strip()); self.profile.save(self.settings.profile_path(self.profile.name))
            self.refresh_builders(safe_name(self.profile.name)); self.tabs.setCurrentIndex(1)
        elif idx == 0:
            self.profile = None
        else:
            self.profile = Profile.load(self.settings.profile_path(txt))
        self.after_profile_change()

    def after_profile_change(self):
        if self.profile:
            k = round(self.profile.inches_per_pt * 72)
            for i, (_, v) in enumerate(SCALES):
                if v == k: self.scale_cb.setCurrentIndex(i)
        self.fill_setup()

    def save_profile(self):
        if not self.profile: QMessageBox.information(self, 'Builder', 'Choose or create a builder first.'); return
        self.profile.save(self.settings.profile_path(self.profile.name))
        self.statusBar().showMessage(f'Saved builder setup "{self.profile.name}".', 5000)

    def on_scale(self, i):
        if self.profile: self.profile.inches_per_pt = SCALES[i][1] / 72

    # ---------- files
    def open_file(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Open builder plan', '', 'Plans (*.pdf *.dwg *.dxf)')
        if f: self.load(f)

    def load(self, f):
        self.result = None; self.region = None; self.pages.clear(); self.sheets = []
        ext = Path(f).suffix.lower()
        if ext == '.pdf':
            doc = pymupdf.open(f)
            for i in range(len(doc)):
                self.sheets.append(PdfSheet(doc, i)); self.pages.addItem(f'Page {i + 1}')
        else:
            exe = oda.find_converter(self.settings.oda_path)
            try: dxf = oda.to_dxf(f, exe)
            except Exception as e: QMessageBox.warning(self, 'Can\'t open DWG', str(e)); return
            self.dwg_doc = dwgin.open_drawing(dxf)
            self.sheets.append(DwgSheet(self.dwg_doc, Path(f).name)); self.pages.addItem(Path(f).name)
        self.setWindowTitle(f'Plan Cleanup {__version__} — {Path(f).name}')
        self.pages.setCurrentRow(0)

    def on_page(self, i):
        if i < 0 or i >= len(self.sheets): return
        self.sheet = self.sheets[i]; self.region = None; self.result = None; self.teacher = None
        self.view.set_sheet(self.sheet.pixmap()); self.view.set_mode('pan')
        self.area_lbl.setText('No plan area yet.'); self.tabs.setCurrentIndex(0); self.fill_setup()

    def on_region(self, rect: QRectF):
        self.region = self.sheet.region_from_view(rect); self.view.set_mode('pan'); self.teacher = None
        self.area_lbl.setText('Plan area set. Next: builder setup (once per builder), then Clean.')
        if self.profile and self.sheet.kind == 'pdf' and not self.profile.pen_roles:
            paths = [p for p in self.sheet.page.get_drawings() if in_region(p, self.region)]
            self.profile.pen_roles = guess_roles(paths)
        self.fill_setup()
        self.tabs.setCurrentIndex(1 if (self.profile is None or not (self.profile.symbols or self.profile.dwg_block_map)) else 2)

    # ---------- setup tab
    def fill_setup(self):
        self.roles.setRowCount(0); self.examples.setRowCount(0)
        if not self.profile or not self.sheet: return
        if self.sheet.kind == 'pdf':
            self.roles_lbl.setText('Line types on this plan (select one to see it highlighted)')
            if not self.region: return
            paths = [p for p in self.sheet.page.get_drawings() if in_region(p, self.region)]
            st = sorted(pens_in(paths).items(), key=lambda kv: -kv[1]['count'])
            for k, s in st:
                self.profile.pen_roles.setdefault(k, 'misc')
                self._role_row(k, s['count'], self.profile.pen_roles, PEN_ROLES)
            self.sym_lbl.setText('Symbol examples (click one of each kind on the plan)')
            for ex in self.profile.symbols: self._example_row(ex)
            self.teach_btn.setEnabled(True)
        else:
            layers, blocks = dwgin.inventory(self.dwg_doc)
            self.roles_lbl.setText('Layers in this drawing')
            for k, n in layers.most_common(): self._role_row(k, n, self.profile.dwg_layer_roles, PEN_ROLES, default='misc')
            self.sym_lbl.setText('Builder blocks: choose what each one is (leave blank to keep as lines)')
            for k, n in blocks.most_common(): self._block_row(k, n)
            self.teach_btn.setEnabled(False)

    def _role_row(self, key, count, store, roles, default='misc'):
        r = self.roles.rowCount(); self.roles.insertRow(r)
        it = QTableWidgetItem(key); it.setData(Qt.UserRole, key); self.roles.setItem(r, 0, it)
        self.roles.setItem(r, 1, QTableWidgetItem(str(count)))
        cb = QComboBox(); cb.addItems(list(roles.values()))
        cb.setCurrentIndex(list(roles).index(store.get(key, default)))
        cb.currentIndexChanged.connect(lambda i, k=key: store.__setitem__(k, list(roles)[i]))
        self.roles.setCellWidget(r, 2, cb)

    def _block_row(self, name, count):
        r = self.examples.rowCount(); self.examples.insertRow(r)
        self.examples.setItem(r, 0, QTableWidgetItem(f'{name}  ({count})'))
        cb = QComboBox(); keys = [''] + [k for k in SYMBOL_TYPES]; cb.addItems(['(keep as lines)'] + [SYMBOL_TYPES[k][0] for k in keys[1:]])
        cur = self.profile.dwg_block_map.get(name, ''); cb.setCurrentIndex(keys.index(cur) if cur in keys else 0)
        def set_(i, n=name):
            if keys[i]: self.profile.dwg_block_map[n] = keys[i]
            else: self.profile.dwg_block_map.pop(n, None)
        cb.currentIndexChanged.connect(set_); self.examples.setCellWidget(r, 1, cb)

    def _example_row(self, ex):
        r = self.examples.rowCount(); self.examples.insertRow(r)
        img = 255 - ex.image().clip(0, 255).astype('uint8')
        q = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_Grayscale8).copy()
        it = QTableWidgetItem(); it.setIcon(QIcon(QPixmap.fromImage(q))); it.setData(Qt.UserRole, ex.id); self.examples.setItem(r, 0, it)
        self.examples.setRowHeight(r, 52)
        cb = QComboBox(); keys = list(SYMBOL_TYPES); cb.addItems([SYMBOL_TYPES[k][0] for k in keys]); cb.setCurrentIndex(keys.index(ex.cls))
        cb.currentIndexChanged.connect(lambda i, e=ex: setattr(e, 'cls', keys[i])); self.examples.setCellWidget(r, 1, cb)

    def on_role_select(self):
        if not self.sheet or self.sheet.kind != 'pdf' or not self.region: return
        rows = {i.row() for i in self.roles.selectedItems()}
        if not rows: return
        key = self.roles.item(min(rows), 0).data(Qt.UserRole)
        self.view.clear_overlay()
        polys = []
        from planclean.vec import polylines
        for p in self.sheet.page.get_drawings():
            if pen_key(p) == key and in_region(p, self.region):
                for q in polylines(p): polys.append([self.sheet.to_view(*v) for v in q])
        self.view.add_paths(polys, '#ff2d55', 2); self.view.page_item.setOpacity(0.35)

    def start_teach(self):
        if not self.profile: QMessageBox.information(self, 'Builder', 'Choose or create a builder first.'); return
        if not self.region: QMessageBox.information(self, 'Plan area', 'Draw a box around the plan first.'); return
        self.view.clear_overlay(); self.view.set_mode('teach')
        self.statusBar().showMessage('Click the centre of a builder symbol. Press Esc when done.')

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape: self.view.set_mode('review' if self.result else 'pan'); self.statusBar().clearMessage()
        else: super().keyPressEvent(e)

    def on_teach_click(self, p: QPointF):
        if self.teacher is None:
            self.teacher = Teacher(self.sheet.page, self.region, self.profile)
        x, y = self.sheet.from_view(p.x(), p.y())
        labels = [v[0] for v in SYMBOL_TYPES.values()]
        choice, ok = QInputDialog.getItem(self, 'What is this symbol?', 'This builder symbol is a:', labels, 0, False)
        if not ok: return
        cls = list(SYMBOL_TYPES)[labels.index(choice)]
        # the pens under a taught symbol are electrical
        for k in self.teacher.pens_near(x, y, 4):
            if self.profile.pen_roles.get(k) in (None, 'misc') and not k.startswith('fill'): self.profile.pen_roles[k] = 'electrical'
        self.teacher.refresh()
        ex, (cx, cy) = self.teacher.example_at(x, y, cls)
        self.profile.symbols.append(ex); self._example_row(ex)
        vx, vy = self.sheet.to_view(cx, cy); self.view.add_marker(vx, vy, CLASS_COLORS.get(cls, '#000'), sym_label(cls).split(' (')[0])

    def remove_example(self):
        rows = sorted({i.row() for i in self.examples.selectedIndexes()}, reverse=True)
        if self.sheet and self.sheet.kind == 'pdf':
            for r in rows:
                eid = self.examples.item(r, 0).data(Qt.UserRole)
                self.profile.symbols = [e for e in self.profile.symbols if e.id != eid]; self.examples.removeRow(r)

    # ---------- clean + review
    def run_clean(self):
        if not (self.sheet and self.region and self.profile):
            QMessageBox.information(self, 'Not ready', 'Open a plan, choose the builder and draw a box around the plan first.'); return
        prof = self.profile
        if self.sheet.kind == 'pdf':
            if not prof.symbols: QMessageBox.information(self, 'Builder setup', 'Teach at least one symbol in step 2 first.'); return
            page, region = self.sheet.page, self.region
            fn = lambda prog: clean_pdf(page, region, prof, progress=prog)
        else:
            doc, region = self.dwg_doc, self.region
            fn = lambda prog: dwgin.clean_dwg(doc, region, prof, progress=prog)
        run_in_thread(self, fn, self.on_cleaned, 'Cleaning the plan…')

    def on_cleaned(self, res: Result):
        self.result = res; self.tabs.setCurrentIndex(2); self.view.set_mode('review')
        from collections import Counter
        c = Counter(s.cls for s in res.symbols)
        parts = ', '.join(f'{n} {sym_label(k).split(" (")[0].lower()}' for k, n in c.most_common())
        self.summary.setText(f'<b>{len(res.symbols)} symbols:</b> {parts}.<br><b>{sum(1 for t in res.texts if t.kind == "room")} room names</b>, '
                             f'{sum(1 for t in res.texts if t.kind == "note")} notes kept, {len(res.dropped_labels)} builder labels removed.')
        self.draw_result(); self.fill_review()
        if self.view.region_item is not None: self.view.fitInView(self.view.region_item, Qt.KeepAspectRatio)

    def _view_pt(self, X, Y):
        return self.sheet.to_view(*self.result.to_page(X, Y))

    def draw_result(self):
        if not self.result: return
        self.view.clear_overlay()
        self.view.page_item.setOpacity(0.25 if self.show_orig.isChecked() else 0.0)
        bg = self.view.scene_.addRect(self.view.sceneRect(), Qt.NoPen, QColor('white')); bg.setZValue(-1); self.view.overlay.append(bg)
        polys = {}
        for role, pts, f in self.result.lines:
            polys.setdefault(role, []).append([self._view_pt(*p) for p in pts])
        for role, ps in polys.items():
            self.view.add_paths(ps, {'wall': '#111', 'wiring': '#555'}.get(role, '#888'), 1.5 if role == 'wall' else 0)
        for s in self.result.symbols:
            if s.cls == 'remove': continue
            x, y = self._view_pt(s.x, s.y)
            col = '#ff0000' if s.review else CLASS_COLORS.get(s.cls, '#000')
            self.view.add_marker(x, y, col, sym_label(s.cls).split(' (')[0].split(' /')[0], payload=s, ring=bool(s.review))
        for t in self.result.texts:
            x, y = self._view_pt(t.x, t.y)
            it = self.view.scene_.addSimpleText(' / '.join(t.lines)); it.setBrush(QColor('#0a5' if t.kind == 'room' else '#a50'))
            it.setPos(x - it.boundingRect().width() / 2, y - 6); it.setZValue(9); it.setData(0, t); self.view.overlay.append(it)

    def fill_review(self):
        self.review.clear()
        for s in self.result.symbols:
            if s.review:
                it = QListWidgetItem(f'{sym_label(s.cls).split(" (")[0]}: {s.review}'); it.setData(Qt.UserRole, s); self.review.addItem(it)
        for t in self.result.texts:
            if t.review:
                it = QListWidgetItem(f'Note "{" ".join(t.lines)}": {t.review}'); it.setData(Qt.UserRole, t); self.review.addItem(it)
        self._sel = None

    def on_review_item(self, it):
        self.on_pick(it.data(Qt.UserRole))

    def on_pick(self, obj):
        self._sel = obj
        if hasattr(obj, 'cls'):
            self.edit_type.setCurrentIndex(list(SYMBOL_TYPES).index(obj.cls))
            x, y = self._view_pt(obj.x, obj.y); self.view.centerOn(x, y)
            self.statusBar().showMessage(f'Selected: {sym_label(obj.cls)} (match {obj.score:.2f}) {" ".join(obj.mods)}')
        else:
            x, y = self._view_pt(obj.x, obj.y); self.view.centerOn(x, y)
            txt, ok = QInputDialog.getText(self, 'Edit text', 'Text (use / for a new line):', text=' / '.join(obj.lines))
            if ok:
                obj.lines = [l.strip() for l in txt.split('/') if l.strip()]; obj.review = ''
                if not obj.lines: self.result.texts.remove(obj)
                self.draw_result(); self.fill_review()

    def change_type(self, i):
        s = getattr(self, '_sel', None)
        if s is None or not hasattr(s, 'cls'): return
        s.cls = list(SYMBOL_TYPES)[i]; s.review = ''; self.draw_result(); self.fill_review()

    def delete_sel(self):
        s = getattr(self, '_sel', None)
        if s is None: return
        if hasattr(s, 'cls'): self.result.symbols.remove(s)
        else: self.result.texts.remove(s)
        self._sel = None; self.draw_result(); self.fill_review()

    def ok_sel(self):
        s = getattr(self, '_sel', None)
        if s is None: return
        s.review = ''; self.draw_result(); self.fill_review()

    # ---------- saving
    def library_blocks(self):
        lib = self.library()
        return sorted(b.name for b in lib.blocks if not b.name.startswith('*')) if lib else []

    def library(self):
        if self._lib is not None: return self._lib
        p = self.settings.block_library
        if not p or not Path(p).exists(): return None
        try:
            dxf = oda.to_dxf(p, oda.find_converter(self.settings.oda_path))
            self._lib = dxfout.load_library(dxf)
        except Exception as e:
            QMessageBox.warning(self, 'Block library', f'Could not read your block library:\n{e}'); return None
        return self._lib

    def open_settings(self):
        d = SettingsDialog(self.settings, self)
        if d.exec():
            old = self.settings.block_library; d.apply()
            if self.settings.block_library != old: self._lib = None
            self.refresh_builders(self.profile and safe_name(self.profile.name))

    def save_result(self):
        if not self.result: return
        lib = self.library()
        if lib is None:
            if QMessageBox.question(self, 'Block library', 'Your block library is not set, so symbols will be placeholders. '
                                    'Set it now in Settings?') == QMessageBox.Yes:
                self.open_settings(); lib = self.library()
        exe = oda.find_converter(self.settings.oda_path)
        flt = 'AutoCAD drawing (*.dwg);;DXF (*.dxf)' if exe else 'DXF (*.dxf)'
        f, chosen = QFileDialog.getSaveFileName(self, 'Save cleaned plan', 'cleaned plan.dwg' if exe else 'cleaned plan.dxf', flt)
        if not f: return
        want_dwg = f.lower().endswith('.dwg') or (exe and 'dwg' in chosen and not f.lower().endswith('.dxf'))
        tmp = Path(tempfile.mkdtemp()) / 'cleaned.dxf'
        missing = dxfout.write_dxf(self.result, str(tmp), lib, self.settings.block_for,
                                   self.settings.text_height_room, self.settings.text_height_note)
        try:
            if want_dwg:
                if not f.lower().endswith('.dwg'): f += '.dwg'
                oda.convert(tmp, f, exe)
            else:
                import shutil; shutil.copy(tmp, f)
        except Exception as e:
            QMessageBox.critical(self, 'Save failed', str(e)); return
        msg = f'Saved {Path(f).name}.'
        if missing: msg += '\n\nThese blocks were not in your library, so placeholders were used: ' + ', '.join(missing)
        if not exe: msg += '\n\nInstall the free ODA File Converter to save DWG directly.'
        QMessageBox.information(self, 'Saved', msg)

def main():
    app = QApplication(sys.argv); app.setApplicationName('Plan Cleanup')
    w = MainWindow(); w.show()
    if len(sys.argv) > 1: w.load(sys.argv[1])
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
